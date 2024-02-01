import io
import json
import re
from pathlib import Path
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import time
import functools
import tempfile
import shutil
import psutil
import copy
import signal
import logging
import traceback
#from pprint import pformat

from multiprocessing import Process
#from threading import Thread
import queue

import cv2
import numpy

from PIL import Image

import piexif
from fractions import Fraction

from . import constants

from .processing import ImageProcessor
from .miscUpload import miscUpload

from .flask import create_app
from .flask import db
from .flask.miscDb import miscDb

from .flask.models import TaskQueueState
from .flask.models import TaskQueueQueue
from .flask.models import IndiAllSkyDbCameraTable
from .flask.models import IndiAllSkyDbImageTable
from .flask.models import IndiAllSkyDbTaskQueueTable

from sqlalchemy import func
#from sqlalchemy.orm.exc import NoResultFound

from .exceptions import TimeOutException
from .exceptions import BadImage



app = create_app()

logger = logging.getLogger('indi_allsky')



class ImageWorker(Process):

    sqm_history_minutes = 30
    stars_history_minutes = 30


    def __init__(
        self,
        idx,
        config,
        error_q,
        image_q,
        upload_q,
        latitude_v,
        longitude_v,
        elevation_v,
        ra_v,
        dec_v,
        exposure_v,
        exposure_min_v,
        exposure_min_day_v,
        exposure_max_v,
        gain_v,
        bin_v,
        sensortemp_v,
        night_v,
        moonmode_v,
    ):
        super(ImageWorker, self).__init__()

        self.name = 'Image-{0:d}'.format(idx)

        self.config = config

        self.error_q = error_q
        self.image_q = image_q
        self.upload_q = upload_q

        self.latitude_v = latitude_v
        self.longitude_v = longitude_v
        self.elevation_v = elevation_v

        self.ra_v = ra_v
        self.dec_v = dec_v

        self.exposure_v = exposure_v
        self.exposure_min_v = exposure_min_v
        self.exposure_min_day_v = exposure_min_day_v
        self.exposure_max_v = exposure_max_v
        self.gain_v = gain_v
        self.bin_v = bin_v
        self.sensortemp_v = sensortemp_v
        self.night_v = night_v
        self.moonmode_v = moonmode_v

        # shared between objects
        self.astrometric_data = {
            'sun_alt'       : 0.0,
            'moon_alt'      : 0.0,
            'moon_phase'    : 0.0,
            'sun_moon_sep'  : 90.0,
            'sidereal_time' : 'unset',
            'moon_up'       : 'unset',
            'mercury_alt'   : 0.0,
            'mercury_up'    : 'unset',
            'venus_alt'     : 0.0,
            'venus_up'      : 'unset',
            'venus_phase'   : 0.0,
            'mars_alt'      : 0.0,
            'mars_up'       : 'unset',
            'jupiter_alt'   : 0.0,
            'jupiter_up'    : 'unset',
            'saturn_alt'    : 0.0,
            'saturn_up'     : 'unset',
            'iss_up'        : 'No data',
            'iss_alt'       : 0.0,
            'iss_next_h'    : 0.0,
            'iss_next_alt'  : 0.0,
            'hst_up'        : 'No data',
            'hst_alt'       : 0.0,
            'hst_next_h'    : 0.0,
            'hst_next_alt'  : 0.0,
        }

        self.filename_t = 'ccd{0:d}_{1:s}.{2:s}'

        self.generate_mask_base = True

        self.target_adu_found = False
        self.current_adu_target = 0
        self.hist_adu = []

        self.sqm_value = 0

        self.image_count = 0
        self.metadata_count = 0

        self.image_processor = ImageProcessor(
            self.config,
            latitude_v,
            longitude_v,
            elevation_v,
            ra_v,
            dec_v,
            exposure_v,
            gain_v,
            bin_v,
            sensortemp_v,
            night_v,
            moonmode_v,
            self.astrometric_data,
        )

        self._miscDb = miscDb(self.config)
        self._miscUpload = miscUpload(self.config, self.upload_q)


        self._libcamera_raw = False

        if self.config['CAMERA_INTERFACE'].startswith('libcamera') and self.config.get('LIBCAMERA', {}).get('IMAGE_FILE_TYPE', '') == 'dng':
            self.libcamera_raw = True
            self.image_processor.libcamera_raw = True


        if self.config.get('IMAGE_FOLDER'):
            self.image_dir = Path(self.config['IMAGE_FOLDER']).absolute()
        else:
            self.image_dir = Path(__file__).parent.parent.joinpath('html', 'images').absolute()

        self._shutdown = False


    @property
    def libcamera_raw(self):
        return self._libcamera_raw

    @libcamera_raw.setter
    def libcamera_raw(self, new_libcamera_raw):
        self._libcamera_raw = bool(new_libcamera_raw)



    def sighup_handler_worker(self, signum, frame):
        logger.warning('Caught HUP signal')

        # set flag for program to stop processes
        self._shutdown = True


    def sigterm_handler_worker(self, signum, frame):
        logger.warning('Caught TERM signal')

        # set flag for program to stop processes
        self._shutdown = True


    def sigint_handler_worker(self, signum, frame):
        logger.warning('Caught INT signal')

        # set flag for program to stop processes
        self._shutdown = True


    def sigalarm_handler_worker(self, signum, frame):
        raise TimeOutException()



    def run(self):
        # setup signal handling after detaching from the main process
        signal.signal(signal.SIGHUP, self.sighup_handler_worker)
        signal.signal(signal.SIGTERM, self.sigterm_handler_worker)
        signal.signal(signal.SIGINT, self.sigint_handler_worker)
        signal.signal(signal.SIGALRM, self.sigalarm_handler_worker)


        ### use this as a method to log uncaught exceptions
        try:
            self.saferun()
        except Exception as e:
            tb = traceback.format_exc()
            self.error_q.put((str(e), tb))
            raise e



    def saferun(self):
        #raise Exception('Test exception handling in worker')

        while True:
            try:
                i_dict = self.image_q.get(timeout=23)  # prime number
            except queue.Empty:
                continue


            if i_dict.get('stop'):
                logger.warning('Goodbye')
                return

            if self._shutdown:
                logger.warning('Goodbye')
                return


            # new context for every task, reduces the effects of caching
            with app.app_context():
                self.processImage(i_dict)


    def processImage(self, i_dict):
        ### Not using DB task queue for image processing to reduce database I/O
        #task_id = i_dict['task_id']

        #try:
        #    task = IndiAllSkyDbTaskQueueTable.query\
        #        .filter(IndiAllSkyDbTaskQueueTable.id == task_id)\
        #        .filter(IndiAllSkyDbTaskQueueTable.state == TaskQueueState.QUEUED)\
        #        .filter(IndiAllSkyDbTaskQueueTable.queue == TaskQueueQueue.IMAGE)\
        #        .one()

        #except NoResultFound:
        #    logger.error('Task ID %d not found', task_id)
        #    continue


        #task.setRunning()


        #filename = Path(task.data['filename'])
        #exposure = task.data['exposure']
        #exp_date = datetime.fromtimestamp(task.data['exp_time'])
        #exp_elapsed = task.data['exp_elapsed']
        #camera_id = task.data['camera_id']
        #filename_t = task.data.get('filename_t')
        ###

        filename_p = Path(i_dict['filename'])
        exposure = i_dict['exposure']
        exp_date = datetime.fromtimestamp(i_dict['exp_time'])
        exp_elapsed = i_dict['exp_elapsed']
        camera_id = i_dict['camera_id']
        filename_t = i_dict.get('filename_t')

        # libcamera
        libcamera_black_level = i_dict.get('libcamera_black_level', 0)
        libcamera_awb_gains = i_dict.get('libcamera_awb_gains')
        libcamera_ccm = i_dict.get('libcamera_ccm')


        if filename_t:
            self.filename_t = filename_t


        if not filename_p.exists():
            logger.error('Frame not found: %s', filename_p)
            #task.setFailed('Frame not found: {0:s}'.format(str(filename_p)))
            return


        if filename_p.stat().st_size == 0:
            logger.error('Frame is empty: %s', filename_p)
            filename_p.unlink()
            return


        camera = IndiAllSkyDbCameraTable.query\
            .filter(IndiAllSkyDbCameraTable.id == camera_id)\
            .one()


        processing_start = time.time()


        self.image_processor.get_astrometric_data()


        try:
            i_ref = self.image_processor.add(filename_p, exposure, exp_date, exp_elapsed, camera)
        except BadImage as e:
            logger.error('Bad Image: %s', str(e))
            filename_p.unlink()
            #task.setFailed('Bad Image: {0:s}'.format(str(filename_p)))
            return


        filename_p.unlink()  # original file is no longer needed


        self.image_count += 1


        # use original value if not defined
        libcamera_black_level = i_ref.get('libcamera_black_level', libcamera_black_level)


        self.image_processor.calibrate(libcamera_black_level=libcamera_black_level)


        if self.config.get('IMAGE_SAVE_FITS'):
            i_ref = self.image_processor.getLatestImage()
            self.write_fit(i_ref, camera)


        self.image_processor.calculateSqm()

        self.image_processor.stack()

        self.image_processor.debayer()



        image_height, image_width = self.image_processor.image.shape[:2]
        logger.info('Image: %d x %d', image_width, image_height)


        i_ref = self.image_processor.getLatestImage()

        ### IMAGE IS CALIBRATED ###


        ### EXIF tags ###
        exp_date_utc = exp_date.replace(tzinfo=timezone.utc)

        # Python 3.6, 3.7 does not support as_integer_ratio()
        focal_length_frac = Fraction(camera.lensFocalLength).limit_denominator()
        focal_length = (focal_length_frac.numerator, focal_length_frac.denominator)

        f_number_frac = Fraction(camera.lensFocalRatio).limit_denominator()
        f_number = (f_number_frac.numerator, f_number_frac.denominator)

        exposure_time_frac = Fraction(exposure).limit_denominator(max_denominator=31250)
        exposure_time = (exposure_time_frac.numerator, exposure_time_frac.denominator)

        zeroth_ifd = {
            piexif.ImageIFD.Model            : camera.name,
            piexif.ImageIFD.Software         : 'indi-allsky',
            piexif.ImageIFD.ExposureTime     : exposure_time,
        }
        exif_ifd = {
            piexif.ExifIFD.DateTimeOriginal  : exp_date_utc.strftime('%Y:%m:%d %H:%M:%S'),
            piexif.ExifIFD.LensModel         : camera.lensName,
            piexif.ExifIFD.LensSpecification : (focal_length, focal_length, f_number, f_number),
            piexif.ExifIFD.FocalLength       : focal_length,
            piexif.ExifIFD.FNumber           : f_number,
            #piexif.ExifIFD.ApertureValue  # this is not the Aperture size
        }


        if self.sensortemp_v.value > -150:
            # Add temperature data
            temperature_frac = Fraction(self.sensortemp_v.value).limit_denominator()
            exif_ifd[piexif.ExifIFD.Temperature] = (temperature_frac.numerator, temperature_frac.denominator)


        jpeg_exif_dict = {
            '0th'   : zeroth_ifd,
            'Exif'  : exif_ifd,
        }


        if not self.config.get('IMAGE_EXIF_PRIVACY'):
            if camera.owner:
                zeroth_ifd[piexif.ImageIFD.Copyright] = camera.owner


            long_deg, long_min, long_sec = self.decdeg2dms(camera.longitude)
            lat_deg, lat_min, lat_sec = self.decdeg2dms(camera.latitude)

            if long_deg < 0:
                long_ref = 'W'
            else:
                long_ref = 'E'

            if lat_deg < 0:
                lat_ref = 'S'
            else:
                lat_ref = 'N'

            gps_datestamp = exp_date_utc.strftime('%Y:%m:%d')
            gps_hour   = int(exp_date_utc.strftime('%H'))
            gps_minute = int(exp_date_utc.strftime('%M'))
            gps_second = int(exp_date_utc.strftime('%S'))

            gps_ifd = {
                piexif.GPSIFD.GPSVersionID       : (2, 2, 0, 0),
                piexif.GPSIFD.GPSDateStamp       : gps_datestamp,
                piexif.GPSIFD.GPSTimeStamp       : ((gps_hour, 1), (gps_minute, 1), (gps_second, 1)),
                piexif.GPSIFD.GPSLongitudeRef    : long_ref,
                piexif.GPSIFD.GPSLongitude       : ((int(abs(long_deg)), 1), (int(long_min), 1), (0, 1)),  # no seconds
                piexif.GPSIFD.GPSLatitudeRef     : lat_ref,
                piexif.GPSIFD.GPSLatitude        : ((int(abs(lat_deg)), 1), (int(lat_min), 1), (0, 1)),  # no seconds
                #piexif.GPSIFD.GPSAltitudeRef     : 0,  # 0 = above sea level, 1 = below
                #piexif.GPSIFD.GPSAltitude        : (0, 1),
            }

            jpeg_exif_dict['GPS'] = gps_ifd


        jpeg_exif = piexif.dump(jpeg_exif_dict)


        # only perform this processing if libcamera is set to raw mode
        if self.libcamera_raw:
            # These values come from libcamera
            if libcamera_awb_gains:
                logger.info('Overriding Red balance: %f', libcamera_awb_gains[0])
                logger.info('Overriding Blue balance: %f', libcamera_awb_gains[1])
                self.config['WBR_FACTOR'] = float(libcamera_awb_gains[0])
                self.config['WBB_FACTOR'] = float(libcamera_awb_gains[1])


            # Not quite working
            if libcamera_ccm:
                self.image_processor.apply_color_correction_matrix(libcamera_ccm)


        if self.config.get('IMAGE_EXPORT_RAW'):
            self.export_raw_image(i_ref, jpeg_exif=jpeg_exif)


        # Calculate ADU before stretch
        adu = self.image_processor.calculate_8bit_adu()
        # adu value may be updated below


        self.image_processor.stretch()


        if self.config.get('CONTRAST_ENHANCE_16BIT'):
            if not self.night_v.value and self.config['DAYTIME_CONTRAST_ENHANCE']:
                # Contrast enhancement during the day
                self.image_processor.contrast_clahe_16bit()
            elif self.night_v.value and self.config['NIGHT_CONTRAST_ENHANCE']:
                # Contrast enhancement during night
                self.image_processor.contrast_clahe_16bit()


        self.image_processor.convert_16bit_to_8bit()


        #with io.open('/tmp/indi_allsky_numpy.npy', 'w+b') as f_numpy:
        #    numpy.save(f_numpy, self.image_processor.image)
        #logger.info('Wrote Numpy data: /tmp/indi_allsky_numpy.npy')


        # adu calculate (before processing)
        adu, adu_average = self.calculate_exposure(adu, exposure)


        # generate a new mask base once the target ADU is found
        # this should only only fire once per restart
        if self.generate_mask_base and self.target_adu_found:
            self.generate_mask_base = False
            self.write_mask_base_img(self.image_processor.image)


        # line detection
        if self.night_v.value and self.config.get('DETECT_METEORS'):
            self.image_processor.detectLines()


        # star detection
        if self.night_v.value and self.config.get('DETECT_STARS', True):
            self.image_processor.detectStars()


        # additional draw code
        if self.config.get('DETECT_DRAW'):
            self.image_processor.drawDetections()


        if self.config.get('IMAGE_ROTATE'):
            self.image_processor.rotate_90()


        # rotation
        if self.config.get('IMAGE_ROTATE_ANGLE'):
            self.image_processor.rotate_angle()


        # verticle flip
        if self.config.get('IMAGE_FLIP_V'):
            self.image_processor.flip_v()

        # horizontal flip
        if self.config.get('IMAGE_FLIP_H'):
            self.image_processor.flip_h()


        # crop
        if self.config.get('IMAGE_CROP_ROI'):
            self.image_processor.crop_image()


        # green removal
        if self.config.get('SCNR_ALGORITHM'):
            self.image_processor.scnr()


        # white balance
        self.image_processor.white_balance_manual_bgr()

        if self.config.get('AUTO_WB'):
            self.image_processor.white_balance_auto_bgr()


        # saturation
        self.image_processor.saturation_adjust()


        if not self.config.get('CONTRAST_ENHANCE_16BIT'):
            if not self.night_v.value and self.config['DAYTIME_CONTRAST_ENHANCE']:
                # Contrast enhancement during the day
                self.image_processor.contrast_clahe()
            elif self.night_v.value and self.config['NIGHT_CONTRAST_ENHANCE']:
                # Contrast enhancement during night
                self.image_processor.contrast_clahe()


        self.image_processor.colorize()


        self.image_processor.apply_image_circle_mask()


        self.image_processor.apply_logo_overlay()


        if self.config['IMAGE_SCALE'] and self.config['IMAGE_SCALE'] != 100:
            self.image_processor.scale_image()


        # blur
        #self.image_processor.median_blur()

        # denoise
        #self.image_processor.fastDenoise()


        if self.config.get('FISH2PANO', {}).get('ENABLE'):
            if not self.image_count % self.config.get('FISH2PANO', {}).get('MODULUS', 4):
                pano_data = self.image_processor.fish2pano()

                self.write_panorama_img(pano_data, i_ref, camera, jpeg_exif=jpeg_exif)



        self.image_processor.orb_image()

        self.image_processor.cardinal_dirs_label()

        self.image_processor.label_image()


        processing_elapsed_s = time.time() - processing_start
        logger.info('Image processed in %0.4f s', processing_elapsed_s)


        # need this after resizing and scaling
        final_height, final_width = self.image_processor.image.shape[:2]


        #task.setSuccess('Image processed')

        self.write_status_json(i_ref, adu, adu_average)  # write json status file

        latest_file, new_filename = self.write_img(self.image_processor.image, i_ref, camera, jpeg_exif=jpeg_exif)

        if new_filename:
            image_metadata = {
                'type'            : constants.IMAGE,
                'createDate'      : exp_date.timestamp(),
                'exposure'        : exposure,
                'exp_elapsed'     : exp_elapsed,
                'gain'            : self.gain_v.value,
                'binmode'         : self.bin_v.value,
                'temp'            : self.sensortemp_v.value,
                'adu'             : adu,
                'stable'          : self.target_adu_found,
                'moonmode'        : bool(self.moonmode_v.value),
                'moonphase'       : self.astrometric_data['moon_phase'],
                'night'           : bool(self.night_v.value),
                'adu_roi'         : self.config['ADU_ROI'],
                'calibrated'      : i_ref['calibrated'],
                'sqm'             : i_ref['sqm_value'],
                'stars'           : len(i_ref['stars']),
                'detections'      : len(i_ref['lines']),
                'process_elapsed' : processing_elapsed_s,
                'kpindex'         : i_ref['kpindex'],
                'ovation_max'     : i_ref['ovation_max'],
                'smoke_rating'    : i_ref['smoke_rating'],
                'height'          : final_height,
                'width'           : final_width,
                'camera_uuid'     : i_ref['camera_uuid'],
            }

            image_metadata['data'] = {}

            image_entry = self._miscDb.addImage(
                new_filename.relative_to(self.image_dir),
                camera_id,
                image_metadata,
            )


            #image_thumbnail_metadata = {
            #    'type'       : constants.THUMBNAIL,
            #    'createDate' : exp_date.timestamp(),
            #    'night'      : bool(self.night_v.value),
            #    'camera_uuid': camera.uuid,
            #}

            #image_thumbnail_entry = self._miscDb.addThumbnail(
            #    image_entry,
            #    image_metadata,
            #    camera.id,
            #    image_thumbnail_metadata,
            #    numpy_data=self.image_processor.image,
            #)
        else:
            # images not being saved
            image_entry = None
            image_metadata = {}
            #image_thumbnail_entry = None
            #image_thumbnail_metadata = {}


        if latest_file:
            # build mqtt data
            mq_topic_latest = 'latest'

            mqtt_data = {
                'exposure' : round(exposure, 6),
                'gain'     : self.gain_v.value,
                'bin'      : self.bin_v.value,
                'temp'     : round(self.sensortemp_v.value, 1),
                'sunalt'   : round(self.astrometric_data['sun_alt'], 1),
                'moonalt'  : round(self.astrometric_data['moon_alt'], 1),
                'moonphase': round(self.astrometric_data['moon_phase'], 1),
                'moonmode' : bool(self.moonmode_v.value),
                'night'    : bool(self.night_v.value),
                'sqm'      : round(i_ref['sqm_value'], 1),
                'stars'    : len(i_ref['stars']),
                'latitude' : round(self.latitude_v.value, 3),
                'longitude': round(self.longitude_v.value, 3),
                'elevation': self.elevation_v.value,
                'kpindex'  : round(i_ref['kpindex'], 2),
                'ovation_max'  : int(i_ref['ovation_max']),
                'smoke_rating' : constants.SMOKE_RATING_MAP_STR[i_ref['smoke_rating']],
                'sidereal_time': self.astrometric_data['sidereal_time'],
            }


            # publish cpu info
            cpu_info = psutil.cpu_times_percent()
            mqtt_data['cpu/user'] = round(cpu_info.user, 1)
            mqtt_data['cpu/system'] = round(cpu_info.system, 1)
            mqtt_data['cpu/nice'] = round(cpu_info.nice, 1)
            mqtt_data['cpu/iowait'] = round(cpu_info.iowait, 1)  # io wait is not true cpu usage, not including in total
            mqtt_data['cpu/total'] = round(cpu_info.user + cpu_info.system + cpu_info.nice, 1)


            # publish memory info
            memory_info = psutil.virtual_memory()
            memory_total = memory_info.total
            memory_free = memory_info.free

            mqtt_data['memory/user'] = round((memory_info.used / memory_total) * 100.0, 1)
            mqtt_data['memory/cached'] = round((memory_info.cached / memory_total) * 100.0, 1)
            mqtt_data['memory/total'] = round(100 - ((memory_free * 100) / memory_total), 1)


            # publish disk info
            fs_list = psutil.disk_partitions()

            for fs in fs_list:
                if fs.mountpoint.startswith('/snap/'):
                    # skip snap filesystems
                    continue

                try:
                    disk_usage = psutil.disk_usage(fs.mountpoint)
                except PermissionError as e:
                    logger.error('PermissionError: %s', str(e))
                    continue

                if fs.mountpoint == '/':
                    mqtt_data['disk/root'] = round(disk_usage.percent, 1)  # hopefully there is not a /root filesystem
                    continue
                else:
                    # slash is included with filesystem name
                    mqtt_data['disk{0:s}'.format(fs.mountpoint)] = round(disk_usage.percent, 1)


            # publish temperature info
            temp_info = psutil.sensors_temperatures()

            for t_key in temp_info.keys():
                for i, t in enumerate(temp_info[t_key]):
                    if self.config.get('TEMP_DISPLAY') == 'f':
                        current_temp = round(((t.current * 9.0 ) / 5.0) + 32, 1)
                    elif self.config.get('TEMP_DISPLAY') == 'k':
                        current_temp = round(t.current + 273.15, 1)
                    else:
                        current_temp = round(float(t.current), 1)

                    if not t.label:
                        # use index for label name
                        label = str(i)
                    else:
                        label = t.label

                    topic = 'temp/{0:s}/{1:s}'.format(t_key, label)

                    # no spaces, etc in topics
                    topic_sub = re.sub(r'[#+\$\*\>\.\ ]', '_', topic)

                    mqtt_data[topic_sub] = current_temp



            if new_filename:
                upload_filename = new_filename
            else:
                upload_filename = latest_file


            ### upload thumbnail first
            #if image_thumbnail_entry:
            #    self._miscUpload.s3_upload_thumbnail(image_thumbnail_entry, image_thumbnail_metadata)
            #    self._miscUpload.syncapi_thumbnail(image_thumbnail_entry, image_thumbnail_metadata)


            self._miscUpload.s3_upload_image(image_entry, image_metadata)
            self._miscUpload.syncapi_image(image_entry, image_metadata)
            self._miscUpload.mqtt_publish_image(upload_filename, mq_topic_latest, mqtt_data)
            self._miscUpload.upload_image(image_entry)

            self.upload_metadata(i_ref, adu, adu_average)


    def decdeg2dms(self, dd):
        is_positive = dd >= 0
        dd = abs(dd)
        minutes, seconds = divmod(dd * 3600, 60)
        degrees, minutes = divmod(minutes, 60)
        degrees = degrees if is_positive else -degrees
        return degrees, minutes, seconds


    def upload_metadata(self, i_ref, adu, adu_average):
        ### upload images
        if not self.config.get('FILETRANSFER', {}).get('UPLOAD_METADATA'):
            #logger.warning('Metadata uploading disabled')
            return

        if not self.config.get('FILETRANSFER', {}).get('UPLOAD_IMAGE'):
            logger.warning('Metadata uploading disabled when image upload is disabled')
            return


        self.metadata_count += 1

        metadata_remain = self.metadata_count % int(self.config['FILETRANSFER']['UPLOAD_IMAGE'])
        if metadata_remain != 0:
            #next_metadata = int(self.config['FILETRANSFER']['UPLOAD_IMAGE']) - image_metadata
            #logger.info('Next metadata upload in %d images (%d s)', next_metadata, int(self.config['EXPOSURE_PERIOD'] * next_metadata))
            return


        metadata = {
            'type'                : constants.METADATA,
            'device'              : i_ref['camera_name'],
            'night'               : self.night_v.value,
            'temp'                : self.sensortemp_v.value,
            'gain'                : self.gain_v.value,
            'exposure'            : i_ref['exposure'],
            'stable_exposure'     : int(self.target_adu_found),
            'target_adu'          : i_ref['target_adu'],
            'current_adu_target'  : self.current_adu_target,
            'current_adu'         : adu,
            'adu_average'         : adu_average,
            'sqm'                 : i_ref['sqm_value'],
            'stars'               : len(i_ref['stars']),
            'time'                : i_ref['exp_date'].strftime('%s'),
            'sqm_data'            : self.getSqmData(i_ref['camera_id']),
            'stars_data'          : self.getStarsData(i_ref['camera_id']),
            'latitude'            : self.latitude_v.value,
            'longitude'           : self.longitude_v.value,
            'elevation'           : self.elevation_v.value,
            'sidereal_time'       : self.astrometric_data['sidereal_time'],
            'kpindex'             : i_ref['kpindex'],
            'ovation_max'         : i_ref['ovation_max'],
            'smoke_rating'        : constants.SMOKE_RATING_MAP_STR[i_ref['smoke_rating']],
        }


        f_tmp_metadata = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')

        json.dump(metadata, f_tmp_metadata, indent=4)

        f_tmp_metadata.flush()
        f_tmp_metadata.close()

        tmp_metadata_name_p = Path(f_tmp_metadata.name)
        tmp_metadata_name_p.chmod(0o644)


        file_data_dict = {
            'timestamp'    : i_ref['exp_date'],
            'ts'           : i_ref['exp_date'],  # shortcut
        }

        # Replace parameters in names
        remote_dir = self.config['FILETRANSFER']['REMOTE_METADATA_FOLDER'].format(**file_data_dict)
        remote_file = self.config['FILETRANSFER']['REMOTE_METADATA_NAME'].format(**file_data_dict)

        remote_file_p = Path(remote_dir).joinpath(remote_file)

        # tell worker to upload file
        jobdata = {
            'action'       : constants.TRANSFER_UPLOAD,
            'local_file'   : str(tmp_metadata_name_p),
            'remote_file'  : str(remote_file_p),
            'remove_local' : True,
        }

        upload_task = IndiAllSkyDbTaskQueueTable(
            queue=TaskQueueQueue.UPLOAD,
            state=TaskQueueState.QUEUED,
            data=jobdata,
        )
        db.session.add(upload_task)
        db.session.commit()

        self.upload_q.put({'task_id' : upload_task.id})




    def getSqmData(self, camera_id):
        now_minus_minutes = datetime.now() - timedelta(minutes=self.sqm_history_minutes)

        sqm_images = IndiAllSkyDbImageTable.query\
            .add_columns(
                func.max(IndiAllSkyDbImageTable.sqm).label('image_max_sqm'),
                func.min(IndiAllSkyDbImageTable.sqm).label('image_min_sqm'),
                func.avg(IndiAllSkyDbImageTable.sqm).label('image_avg_sqm'),
            )\
            .join(IndiAllSkyDbCameraTable)\
            .filter(IndiAllSkyDbCameraTable.id == camera_id)\
            .filter(IndiAllSkyDbImageTable.createDate > now_minus_minutes)\
            .first()


        sqm_data = {
            'max' : sqm_images.image_max_sqm,
            'min' : sqm_images.image_min_sqm,
            'avg' : sqm_images.image_avg_sqm,
        }

        return sqm_data


    def getStarsData(self, camera_id):
        now_minus_minutes = datetime.now() - timedelta(minutes=self.stars_history_minutes)

        stars_images = IndiAllSkyDbImageTable.query\
            .add_columns(
                func.max(IndiAllSkyDbImageTable.stars).label('image_max_stars'),
                func.min(IndiAllSkyDbImageTable.stars).label('image_min_stars'),
                func.avg(IndiAllSkyDbImageTable.stars).label('image_avg_stars'),
            )\
            .join(IndiAllSkyDbCameraTable)\
            .filter(IndiAllSkyDbCameraTable.id == camera_id)\
            .filter(IndiAllSkyDbImageTable.createDate > now_minus_minutes)\
            .first()


        stars_data = {
            'max' : stars_images.image_max_stars,
            'min' : stars_images.image_min_stars,
            'avg' : stars_images.image_avg_stars,
        }

        return stars_data


    def write_fit(self, i_ref, camera):
        data = i_ref['hdulist'][0].data
        image_height, image_width = data.shape[:2]


        f_tmpfile = tempfile.NamedTemporaryFile(mode='w+b', delete=False, suffix='.fit')

        i_ref['hdulist'].writeto(f_tmpfile)
        f_tmpfile.close()

        tmpfile_p = Path(f_tmpfile.name)


        date_str = i_ref['exp_date'].strftime('%Y%m%d_%H%M%S')
        # raw light
        folder = self.getImageFolder(i_ref['exp_date'], camera)
        filename = folder.joinpath(self.filename_t.format(
            i_ref['camera_id'],
            date_str,
            'fit',
        ))


        fits_metadata = {
            'type'       : constants.FITS_IMAGE,
            'createDate' : i_ref['exp_date'].timestamp(),
            'exposure'   : i_ref['exposure'],
            'gain'       : self.gain_v.value,
            'binmode'    : self.bin_v.value,
            'night'      : bool(self.night_v.value),
            'height'     : image_height,
            'width'      : image_width,
            'camera_uuid': i_ref['camera_uuid'],
        }

        fits_metadata['data'] = {
            'moonmode'        : bool(self.moonmode_v.value),
            'moonphase'       : self.astrometric_data['moon_phase'],
            'sqm'             : i_ref['sqm_value'],
            'stars'           : len(i_ref['stars']),
            'detections'      : len(i_ref['lines']),
            'kpindex'         : i_ref['kpindex'],
            'ovation_max'     : i_ref['ovation_max'],
            'smoke_rating'    : i_ref['smoke_rating'],
        }

        fits_entry = self._miscDb.addFitsImage(
            filename.relative_to(self.image_dir),
            i_ref['camera_id'],
            fits_metadata,
        )


        file_dir = filename.parent
        if not file_dir.exists():
            file_dir.mkdir(mode=0o755, parents=True)

        logger.info('fit filename: %s', filename)


        if filename.exists():
            logger.error('File exists: %s (skipping)', filename)
            tmpfile_p.unlink()
            return


        shutil.copy2(str(tmpfile_p), str(filename))
        filename.chmod(0o644)

        # set mtime to original exposure time
        #os.utime(str(filename), (i_ref['exp_date'].timestamp(), i_ref['exp_date'].timestamp()))

        tmpfile_p.unlink()

        self._miscUpload.s3_upload_fits(fits_entry, fits_metadata)


    def export_raw_image(self, i_ref, jpeg_exif=None):
        if not self.config.get('IMAGE_EXPORT_RAW'):
            return

        if not self.config.get('IMAGE_EXPORT_FOLDER'):
            logger.error('IMAGE_EXPORT_FOLDER not defined')
            return


        f_tmpfile = tempfile.NamedTemporaryFile(mode='w+b', delete=False, suffix='.{0}'.format(self.config['IMAGE_EXPORT_RAW']))
        f_tmpfile.close()

        tmpfile_name = Path(f_tmpfile.name)


        data = self.image_processor.non_stacked_image
        image_height, image_width = data.shape[:2]
        max_bit_depth = self.image_processor.max_bit_depth

        if i_ref['image_bitpix'] == 8:
            # nothing to scale
            scaled_data = data
        elif i_ref['image_bitpix'] == 16:
            logger.info('Upscaling data from %d to 16 bit', max_bit_depth)
            shift_factor = 16 - max_bit_depth
            scaled_data = numpy.left_shift(data, shift_factor)
        else:
            raise Exception('Unsupported bit depth')


        #logger.info('Image type: %s', str(scaled_data.dtype))
        #logger.info('Image shape: %s', str(scaled_data.shape))


        if not self.config.get('IMAGE_EXPORT_FLIP_V'):
            scaled_data = self.image_processor._flip(scaled_data, 0)

        if not self.config.get('IMAGE_EXPORT_FLIP_H'):
            scaled_data = self.image_processor._flip(scaled_data, 1)


        write_img_start = time.time()

        if self.config['IMAGE_EXPORT_RAW'] in ('jpg', 'jpeg'):
            if i_ref['image_bitpix'] == 8:
                scaled_data_8 = scaled_data
            else:
                # jpeg has to be 8 bits
                logger.info('Resampling image from %d to 8 bits', i_ref['image_bitpix'])

                #div_factor = int((2 ** max_bit_depth) / 255)
                #scaled_data_8 = (scaled_data / div_factor).astype(numpy.uint8)

                # shifting is 5x faster than division
                shift_factor = max_bit_depth - 8
                scaled_data_8 = numpy.right_shift(scaled_data, shift_factor).astype(numpy.uint8)

            if len(scaled_data_8.shape) == 2:
                img = Image.fromarray(scaled_data_8)
            else:
                img = Image.fromarray(cv2.cvtColor(scaled_data_8, cv2.COLOR_BGR2RGB))

            img.save(str(tmpfile_name), quality=self.config['IMAGE_FILE_COMPRESSION']['jpg'], exif=jpeg_exif)
        elif self.config['IMAGE_EXPORT_RAW'] in ('png',):
            # Pillow does not support 16-bit RGB data
            # opencv is faster than Pillow with PNG
            cv2.imwrite(str(tmpfile_name), scaled_data, [cv2.IMWRITE_PNG_COMPRESSION, self.config['IMAGE_FILE_COMPRESSION']['png']])
        elif self.config['IMAGE_EXPORT_RAW'] in ('jp2',):
            cv2.imwrite(str(tmpfile_name), scaled_data)
        elif self.config['IMAGE_EXPORT_RAW'] in ('webp',):
            cv2.imwrite(str(tmpfile_name), scaled_data, [cv2.IMWRITE_WEBP_QUALITY, 101])  # lossless
        elif self.config['IMAGE_EXPORT_RAW'] in ('tif', 'tiff'):
            # Pillow does not support 16-bit RGB data
            cv2.imwrite(str(tmpfile_name), scaled_data, [cv2.IMWRITE_TIFF_COMPRESSION, 5])  # LZW
        else:
            raise Exception('Unknown file type: %s', self.config['IMAGE_EXPORT_RAW'])

        write_img_elapsed_s = time.time() - write_img_start
        logger.info('Raw image written in %0.4f s', write_img_elapsed_s)



        export_dir = Path(self.config['IMAGE_EXPORT_FOLDER'])

        if self.night_v.value:
            # images should be written to previous day's folder until noon
            day_ref = i_ref['exp_date'] - timedelta(hours=12)
            timeofday_str = 'night'
        else:
            # daytime
            # images should be written to current day's folder
            day_ref = i_ref['exp_date']
            timeofday_str = 'day'

        date_str = i_ref['exp_date'].strftime('%Y%m%d_%H%M%S')

        hour_str = i_ref['exp_date'].strftime('%d_%H')

        day_folder = export_dir.joinpath('{0:s}'.format(day_ref.strftime('%Y%m%d')), timeofday_str)
        if not day_folder.exists():
            day_folder.mkdir(mode=0o755, parents=True)

        hour_folder = day_folder.joinpath('{0:s}'.format(hour_str))
        if not hour_folder.exists():
            hour_folder.mkdir(mode=0o755)


        raw_filename_t = 'raw_{0:s}'.format(self.filename_t)
        filename = hour_folder.joinpath(raw_filename_t.format(
            i_ref['camera_id'],
            date_str,
            self.config['IMAGE_EXPORT_RAW'],  # file suffix
        ))


        raw_metadata = {
            'type'       : constants.RAW_IMAGE,
            'createDate' : i_ref['exp_date'].timestamp(),
            'exposure'   : i_ref['exposure'],
            'gain'       : self.gain_v.value,
            'binmode'    : self.bin_v.value,
            'night'      : bool(self.night_v.value),
            'height'     : image_height,
            'width'      : image_width,
            'camera_uuid': i_ref['camera_uuid'],
        }

        raw_metadata['data'] = {
            'moonmode'        : bool(self.moonmode_v.value),
            'moonphase'       : self.astrometric_data['moon_phase'],
            'sqm'             : i_ref['sqm_value'],
            'stars'           : len(i_ref['stars']),
            'detections'      : len(i_ref['lines']),
            'kpindex'         : i_ref['kpindex'],
            'ovation_max'     : i_ref['ovation_max'],
            'smoke_rating'    : i_ref['smoke_rating'],
        }

        try:
            raw_filename = filename.relative_to(self.image_dir)
        except ValueError:
            # raw exports may be outside the image path
            raw_filename = filename

        raw_entry = self._miscDb.addRawImage(
            raw_filename,
            i_ref['camera_id'],
            raw_metadata,
        )


        logger.info('RAW filename: %s', filename)

        if filename.exists():
            logger.error('File exists: %s (skipping)', filename)
            tmpfile_name.unlink()
            return


        shutil.copy2(str(tmpfile_name), str(filename))
        filename.chmod(0o644)

        tmpfile_name.unlink()

        # set mtime to original exposure time
        #os.utime(str(filename), (i_ref['exp_date'].timestamp(), i_ref['exp_date'].timestamp()))

        self._miscUpload.s3_upload_raw(raw_entry, raw_metadata)


    def write_mask_base_img(self, data):
        logger.info('Generating new mask base')
        f_tmpfile = tempfile.NamedTemporaryFile(mode='w+b', delete=False, suffix='.png')
        f_tmpfile.close()

        tmpfile_name = Path(f_tmpfile.name)


        cv2.imwrite(str(tmpfile_name), data, [cv2.IMWRITE_PNG_COMPRESSION, self.config['IMAGE_FILE_COMPRESSION']['png']])

        mask_file = self.image_dir.joinpath('mask_base.png')

        try:
            mask_file.unlink()
        except FileNotFoundError:
            pass


        shutil.copy2(str(tmpfile_name), str(mask_file))
        mask_file.chmod(0o644)


        tmpfile_name.unlink()


    def write_img(self, data, i_ref, camera, jpeg_exif=None):
        f_tmpfile = tempfile.NamedTemporaryFile(mode='w+b', delete=False, suffix='.{0}'.format(self.config['IMAGE_FILE_TYPE']))
        f_tmpfile.close()

        tmpfile_name = Path(f_tmpfile.name)


        write_img_start = time.time()

        # write to temporary file
        if self.config['IMAGE_FILE_TYPE'] in ('jpg', 'jpeg'):
            img_rgb = Image.fromarray(cv2.cvtColor(data, cv2.COLOR_BGR2RGB))
            img_rgb.save(str(tmpfile_name), quality=self.config['IMAGE_FILE_COMPRESSION']['jpg'], exif=jpeg_exif)
        elif self.config['IMAGE_FILE_TYPE'] in ('png',):
            # exif does not appear to work with png
            #img_rgb = Image.fromarray(cv2.cvtColor(data, cv2.COLOR_BGR2RGB))
            #img_rgb.save(str(tmpfile_name), compress_level=self.config['IMAGE_FILE_COMPRESSION']['png'])

            # opencv is faster than Pillow with PNG
            cv2.imwrite(str(tmpfile_name), data, [cv2.IMWRITE_PNG_COMPRESSION, self.config['IMAGE_FILE_COMPRESSION']['png']])
        elif self.config['IMAGE_FILE_TYPE'] in ('webp',):
            img_rgb = Image.fromarray(cv2.cvtColor(data, cv2.COLOR_BGR2RGB))
            img_rgb.save(str(tmpfile_name), quality=90, lossless=False, exif=jpeg_exif)
        elif self.config['IMAGE_FILE_TYPE'] in ('tif', 'tiff'):
            # exif does not appear to work with tiff
            img_rgb = Image.fromarray(cv2.cvtColor(data, cv2.COLOR_BGR2RGB))
            img_rgb.save(str(tmpfile_name), compression='tiff_lzw')
        else:
            tmpfile_name.unlink()
            raise Exception('Unknown file type: %s', self.config['IMAGE_FILE_TYPE'])

        write_img_elapsed_s = time.time() - write_img_start
        logger.info('Image compressed in %0.4f s', write_img_elapsed_s)


        ### Always write the latest file for web access
        latest_file = self.image_dir.joinpath('latest.{0:s}'.format(self.config['IMAGE_FILE_TYPE']))

        try:
            latest_file.unlink()
        except FileNotFoundError:
            pass


        shutil.copy2(str(tmpfile_name), str(latest_file))
        latest_file.chmod(0o644)


        ### disable timelapse images in focus mode
        if self.config.get('FOCUS_MODE', False):
            logger.warning('Focus mode enabled, not saving timelapse image')
            tmpfile_name.unlink()
            return None, None


        ### Do not write daytime image files if daytime timelapse is disabled
        if not self.night_v.value and not self.config['DAYTIME_TIMELAPSE']:
            logger.info('Daytime timelapse is disabled')
            tmpfile_name.unlink()
            return latest_file, None


        ### Write the timelapse file
        folder = self.getImageFolder(i_ref['exp_date'], camera)

        date_str = i_ref['exp_date'].strftime('%Y%m%d_%H%M%S')
        filename = folder.joinpath(self.filename_t.format(i_ref['camera_id'], date_str, self.config['IMAGE_FILE_TYPE']))

        #logger.info('Image filename: %s', filename)

        if filename.exists():
            logger.error('File exists: %s (skipping)', filename)
            tmpfile_name.unlink()
            return latest_file, None


        shutil.copy2(str(tmpfile_name), str(filename))
        filename.chmod(0o644)

        tmpfile_name.unlink()


        # set mtime to original exposure time
        #os.utime(str(filename), (i_ref['exp_date'].timestamp(), i_ref['exp_date'].timestamp()))

        #logger.info('Finished writing files')

        return latest_file, filename


    def write_status_json(self, i_ref, adu, adu_average):
        status = {
            'name'                : 'indi_json',
            'class'               : 'ccd',
            'device'              : i_ref['camera_name'],
            'night'               : self.night_v.value,
            'temp'                : self.sensortemp_v.value,
            'gain'                : self.gain_v.value,
            'exposure'            : i_ref['exposure'],
            'stable_exposure'     : int(self.target_adu_found),
            'target_adu'          : i_ref['target_adu'],
            'current_adu_target'  : self.current_adu_target,
            'current_adu'         : adu,
            'adu_average'         : adu_average,
            'sqm'                 : i_ref['sqm_value'],
            'stars'               : len(i_ref['stars']),
            'time'                : i_ref['exp_date'].strftime('%s'),
            'latitude'            : self.latitude_v.value,
            'longitude'           : self.longitude_v.value,
            'elevation'           : self.elevation_v.value,
        }


        indi_allsky_status_p = Path('/var/lib/indi-allsky/indi_allsky_status.json')

        with io.open(str(indi_allsky_status_p), 'w') as f_indi_status:
            json.dump(status, f_indi_status, indent=4)
            f_indi_status.flush()
            f_indi_status.close()

        indi_allsky_status_p.chmod(0o644)


    def getImageFolder(self, exp_date, camera):
        if self.night_v.value:
            # images should be written to previous day's folder until noon
            day_ref = exp_date - timedelta(hours=12)
            timeofday_str = 'night'
        else:
            # images should be written to current day's folder
            day_ref = exp_date
            timeofday_str = 'day'

        hour_str = exp_date.strftime('%d_%H')

        day_folder = self.image_dir.joinpath('ccd_{0:s}'.format(camera.uuid), '{0:s}'.format(day_ref.strftime('%Y%m%d')), timeofday_str)
        if not day_folder.exists():
            day_folder.mkdir(mode=0o755, parents=True)

        hour_folder = day_folder.joinpath('{0:s}'.format(hour_str))
        if not hour_folder.exists():
            hour_folder.mkdir(mode=0o755)

        return hour_folder


    def write_panorama_img(self, pano_data, i_ref, camera, jpeg_exif=None):
        panorama_height, panorama_width = pano_data.shape[:2]

        f_tmpfile = tempfile.NamedTemporaryFile(mode='w+b', delete=False, suffix='.{0}'.format(self.config['IMAGE_FILE_TYPE']))
        f_tmpfile.close()

        tmpfile_name = Path(f_tmpfile.name)


        write_img_start = time.time()

        # write to temporary file
        if self.config['IMAGE_FILE_TYPE'] in ('jpg', 'jpeg'):
            img_rgb = Image.fromarray(cv2.cvtColor(pano_data, cv2.COLOR_BGR2RGB))
            img_rgb.save(str(tmpfile_name), quality=self.config['IMAGE_FILE_COMPRESSION']['jpg'], exif=jpeg_exif)
        elif self.config['IMAGE_FILE_TYPE'] in ('png',):
            # exif does not appear to work with png
            #img_rgb = Image.fromarray(cv2.cvtColor(data, cv2.COLOR_BGR2RGB))
            #img_rgb.save(str(tmpfile_name), compress_level=self.config['IMAGE_FILE_COMPRESSION']['png'])

            # opencv is faster than Pillow with PNG
            cv2.imwrite(str(tmpfile_name), pano_data, [cv2.IMWRITE_PNG_COMPRESSION, self.config['IMAGE_FILE_COMPRESSION']['png']])
        elif self.config['IMAGE_FILE_TYPE'] in ('webp',):
            img_rgb = Image.fromarray(cv2.cvtColor(pano_data, cv2.COLOR_BGR2RGB))
            img_rgb.save(str(tmpfile_name), quality=90, lossless=False, exif=jpeg_exif)
        elif self.config['IMAGE_FILE_TYPE'] in ('tif', 'tiff'):
            # exif does not appear to work with tiff
            img_rgb = Image.fromarray(cv2.cvtColor(pano_data, cv2.COLOR_BGR2RGB))
            img_rgb.save(str(tmpfile_name), compression='tiff_lzw')
        else:
            tmpfile_name.unlink()
            raise Exception('Unknown file type: %s', self.config['IMAGE_FILE_TYPE'])

        write_img_elapsed_s = time.time() - write_img_start
        logger.info('Panorama image compressed in %0.4f s', write_img_elapsed_s)


        ### Always write the latest file for web access
        latest_pano_file = self.image_dir.joinpath('panorama.{0:s}'.format(self.config['IMAGE_FILE_TYPE']))

        try:
            latest_pano_file.unlink()
        except FileNotFoundError:
            pass


        shutil.copy2(str(tmpfile_name), str(latest_pano_file))
        latest_pano_file.chmod(0o644)


        ### disable timelapse images in focus mode
        if self.config.get('FOCUS_MODE', False):
            logger.warning('Focus mode enabled, not saving timelapse image')
            tmpfile_name.unlink()
            return


        ### Do not write daytime image files if daytime timelapse is disabled
        if not self.night_v.value and not self.config['DAYTIME_TIMELAPSE']:
            tmpfile_name.unlink()
            return


        ### Write the panorama file
        folder = self.getImageFolder(i_ref['exp_date'], camera)


        panorama_filename_t = 'panorama_{0:s}'.format(self.filename_t)
        date_str = i_ref['exp_date'].strftime('%Y%m%d_%H%M%S')
        filename = folder.joinpath(panorama_filename_t.format(i_ref['camera_id'], date_str, self.config['IMAGE_FILE_TYPE']))

        #logger.info('Panorama filename: %s', filename)


        panorama_metadata = {
            'type'       : constants.PANORAMA_IMAGE,
            'createDate' : i_ref['exp_date'].timestamp(),
            'exposure'   : i_ref['exposure'],
            'gain'       : self.gain_v.value,
            'binmode'    : self.bin_v.value,
            'night'      : bool(self.night_v.value),
            'height'     : panorama_height,
            'width'      : panorama_width,
            'camera_uuid': i_ref['camera_uuid'],
        }

        panorama_metadata['data'] = {
            'moonmode'        : bool(self.moonmode_v.value),
            'moonphase'       : self.astrometric_data['moon_phase'],
            'sqm'             : i_ref['sqm_value'],
            'stars'           : len(i_ref['stars']),
            'detections'      : len(i_ref['lines']),
            'kpindex'         : i_ref['kpindex'],
            'ovation_max'     : i_ref['ovation_max'],
            'smoke_rating'    : i_ref['smoke_rating'],
        }


        panorama_entry = self._miscDb.addPanoramaImage(
            filename.relative_to(self.image_dir),
            i_ref['camera_id'],
            panorama_metadata,
        )


        if filename.exists():
            logger.error('File exists: %s (skipping)', filename)
            tmpfile_name.unlink()
            return


        shutil.copy2(str(tmpfile_name), str(filename))
        filename.chmod(0o644)

        tmpfile_name.unlink()


        # set mtime to original exposure time
        #os.utime(str(filename), (i_ref['exp_date'].timestamp(), i_ref['exp_date'].timestamp()))

        self._miscUpload.s3_upload_panorama(panorama_entry, panorama_metadata)
        self._miscUpload.syncapi_panorama(panorama_entry, panorama_metadata)
        self._miscUpload.mqtt_publish_image(filename, 'panorama', {})
        self._miscUpload.upload_panorama(panorama_entry)


    def calculate_exposure(self, adu, exposure):
        if adu <= 0.0:
            # ensure we do not divide by zero
            logger.warning('Zero average, setting a default of 0.1')
            adu = 0.1


        if self.night_v.value:
            target_adu = self.config['TARGET_ADU']
            exposure_min = self.exposure_min_v.value
        else:
            target_adu = self.config['TARGET_ADU_DAY']
            exposure_min = self.exposure_min_day_v.value


        # Brightness when the sun is in view (very short exposures) can change drastically when clouds pass through the view
        # Setting a deviation that is too short can cause exposure flapping
        if exposure < 0.001000:
            # DAY
            adu_dev = float(self.config.get('TARGET_ADU_DEV_DAY', 20))

            target_adu_min = target_adu - adu_dev
            target_adu_max = target_adu + adu_dev
            current_adu_target_min = self.current_adu_target - adu_dev
            current_adu_target_max = self.current_adu_target + adu_dev

            exp_scale_factor = 0.50  # scale exposure calculation
            history_max_vals = 6     # number of entries to use to calculate average
        else:
            # NIGHT
            adu_dev = float(self.config.get('TARGET_ADU_DEV', 10))

            target_adu_min = target_adu - adu_dev
            target_adu_max = target_adu + adu_dev
            current_adu_target_min = self.current_adu_target - adu_dev
            current_adu_target_max = self.current_adu_target + adu_dev

            exp_scale_factor = 1.0  # scale exposure calculation
            history_max_vals = 6    # number of entries to use to calculate average



        if not self.target_adu_found:
            self.recalculate_exposure(exposure, adu, target_adu, target_adu_min, target_adu_max, exposure_min, exp_scale_factor)
            return adu, 0.0


        self.hist_adu.append(adu)
        self.hist_adu = self.hist_adu[(history_max_vals * -1):]  # remove oldest values, up to history_max_vals

        logger.info('Current target ADU: %0.2f (%0.2f/%0.2f)', self.current_adu_target, current_adu_target_min, current_adu_target_max)
        logger.info('Current ADU history: (%d) [%s]', len(self.hist_adu), ', '.join(['{0:0.2f}'.format(x) for x in self.hist_adu]))


        adu_average = functools.reduce(lambda a, b: a + b, self.hist_adu) / len(self.hist_adu)
        logger.info('ADU average: %0.2f', adu_average)


        ### Need at least x values to continue
        if len(self.hist_adu) < history_max_vals:
            return adu, 0.0


        ### only change exposure when 70% of the values exceed the max or minimum
        if adu_average > current_adu_target_max:
            logger.warning('ADU increasing beyond limits, recalculating next exposure')
            self.target_adu_found = False
        elif adu_average < current_adu_target_min:
            logger.warning('ADU decreasing beyond limits, recalculating next exposure')
            self.target_adu_found = False

        return adu, adu_average


    def recalculate_exposure(self, exposure, adu, target_adu, target_adu_min, target_adu_max, exposure_min, exp_scale_factor):

        # Until we reach a good starting point, do not calculate a moving average
        if adu <= target_adu_max and adu >= target_adu_min:
            logger.warning('Found target value for exposure')
            self.current_adu_target = copy.copy(adu)
            self.target_adu_found = True
            self.hist_adu = []
            return


        # Scale the exposure up and down based on targets
        if adu > target_adu_max:
            new_exposure = exposure - ((exposure - (exposure * (target_adu / adu))) * exp_scale_factor)
        elif adu < target_adu_min:
            new_exposure = exposure - ((exposure - (exposure * (target_adu / adu))) * exp_scale_factor)
        else:
            new_exposure = exposure


        # Do not exceed the limits
        if new_exposure < exposure_min:
            new_exposure = float(exposure_min)
        elif new_exposure > self.exposure_max_v.value:
            new_exposure = float(self.exposure_max_v.value)


        logger.warning('New calculated exposure: %0.8f', new_exposure)
        with self.exposure_v.get_lock():
            self.exposure_v.value = new_exposure


