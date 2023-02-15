import logging

logger = logging.getLogger('indi_allsky')


class IndiAllSkyConfig(object):

    _template_config = {
        "CAMERA_INTERFACE" : "indi",
        "INDI_SERVER" : "localhost",
        "INDI_PORT"   : 7624,
        "INDI_CAMERA_NAME" : "",
        "CCD_CONFIG" : {
            "NIGHT" : {
                "GAIN"    : 100,
                "BINNING" : 1
            },
            "MOONMODE" : {
                "GAIN"    : 75,
                "BINNING" : 1
            },
            "DAY" : {
                "GAIN"    : 0,
                "BINNING" : 1
            }
        },
        "INDI_CONFIG_DEFAULTS" : {
            "SWITCHES" : {},
            "PROPERTIES" : {},
            "TEXT" : {}
        },
        "CCD_EXPOSURE_MAX"     : 15.00000,
        "CCD_EXPOSURE_DEF"     : 0.0,
        "CCD_EXPOSURE_MIN"     : 0.0,
        "EXPOSURE_PERIOD"      : 15.00000,
        "EXPOSURE_PERIOD_DAY"  : 15.00000,
        "FOCUS_MODE"           : False,
        "FOCUS_DELAY"          : 4.0,
        "CFA_PATTERN"      : "",  # None, GRBG, RGGB, BGGR, GBRG
        "SCNR_ALGORITHM"   : "",  # empty string, average_neutral, or maximum_neutral
        "WBR_FACTOR"       : 1.0,
        "WBG_FACTOR"       : 1.0,
        "WBB_FACTOR"       : 1.0,
        "AUTO_WB"          : False,
        "CCD_COOLING"      : False,
        "CCD_TEMP"         : 15.0,
        "TEMP_DISPLAY"     : "c",  # c = celcius, f = fahrenheit, k = kelvin",
        "CCD_TEMP_SCRIPT"  : "",
        "GPS_TIMESYNC"     : False,
        "TARGET_ADU" : 75,
        "TARGET_ADU_DEV"     : 10,
        "TARGET_ADU_DEV_DAY" : 20,
        "ADU_ROI" : [],
        "DETECT_STARS" : True,
        "DETECT_STARS_THOLD" : 0.6,
        "DETECT_METEORS" : False,
        "DETECT_MASK" : "",
        "DETECT_DRAW" : False,
        "SQM_ROI" : [],
        "LOCATION_LATITUDE"  : 33,
        "LOCATION_LONGITUDE" : -84,
        "TIMELAPSE_ENABLE"         : True,
        "DAYTIME_CAPTURE"          : True,
        "DAYTIME_TIMELAPSE"        : True,
        "DAYTIME_CONTRAST_ENHANCE" : False,
        "NIGHT_CONTRAST_ENHANCE"   : False,
        "NIGHT_SUN_ALT_DEG"        : -6,
        "NIGHT_MOONMODE_ALT_DEG"   : 0,
        "NIGHT_MOONMODE_PHASE"     : 33,
        "WEB_EXTRA_TEXT" : "",
        "KEOGRAM_ANGLE"    : 0,
        "KEOGRAM_H_SCALE"  : 100,
        "KEOGRAM_V_SCALE"  : 33,
        "KEOGRAM_LABEL"    : True,
        "STARTRAILS_MAX_ADU"    : 50,
        "STARTRAILS_MASK_THOLD" : 190,
        "STARTRAILS_PIXEL_THOLD": 1.0,
        "STARTRAILS_TIMELAPSE"  : True,
        "STARTRAILS_TIMELAPSE_MINFRAMES" : 250,
        "IMAGE_FILE_TYPE" : "jpg",  # jpg, png, or tif
        "IMAGE_FILE_COMPRESSION" : {
            "jpg"   : 90,
            "png"   : 5,
            "tif"   : 5  # 5 = LZW
        },
        "IMAGE_FOLDER"     : "/var/www/html/allsky/images",
        "IMAGE_LABEL"      : True,
        "IMAGE_LABEL_TEMPLATE" : "{timestamp:%Y.%m.%d %H:%M:%S}\nLat {latitude:0.1f} Long {longitude:0.1f}\nExposure {exposure:0.6f}\nGain {gain:d}\nTemp {temp:0.1f}{temp_unit:s}\nStacking {stack_method:s}\nStars {stars:d}",
        "IMAGE_EXTRA_TEXT" : "",
        "IMAGE_CROP_ROI"   : [],
        "IMAGE_ROTATE"     : "",  # empty, ROTATE_90_CLOCKWISE, ROTATE_90_COUNTERCLOCKWISE, ROTATE_180
        "IMAGE_FLIP_V"     : True,
        "IMAGE_FLIP_H"     : True,
        "IMAGE_SCALE"      : 100,
        "NIGHT_GRAYSCALE"  : False,
        "DAYTIME_GRAYSCALE": False,
        "IMAGE_SAVE_FITS"     : False,
        "IMAGE_EXPORT_RAW"    : "",  # png or tif (or empty)
        "IMAGE_EXPORT_FOLDER" : "/var/www/html/allsky/images/export",
        "IMAGE_STACK_METHOD"  : "maximum",  # maximum, average, or minimum
        "IMAGE_STACK_COUNT"   : 1,
        "IMAGE_STACK_ALIGN"   : False,
        "IMAGE_ALIGN_DETECTSIGMA" : 5,
        "IMAGE_ALIGN_POINTS" : 50,
        "IMAGE_ALIGN_SOURCEMINAREA" : 10,
        "IMAGE_STACK_SPLIT"   : False,
        "IMAGE_EXPIRE_DAYS"     : 30,
        "TIMELAPSE_EXPIRE_DAYS" : 365,
        "FFMPEG_FRAMERATE" : 25,
        "FFMPEG_BITRATE"   : "2500k",
        "FFMPEG_VFSCALE"   : "",
        "FFMPEG_CODEC"     : "libx264",
        "FITSHEADERS" : [
            [ "INSTRUME", "indi-allsky" ],
            [ "OBSERVER", "" ],
            [ "SITE", "" ],
            [ "OBJECT", "" ],
            [ "NOTES", "" ]
        ],
        "TEXT_PROPERTIES" : {
            "DATE_FORMAT"    : "%Y%m%d %H:%M:%S",
            "FONT_FACE"      : "FONT_HERSHEY_SIMPLEX",
            "FONT_HEIGHT"    : 30,
            "FONT_X"         : 15,
            "FONT_Y"         : 30,
            "FONT_COLOR"     : [200, 200, 200],
            "FONT_AA"        : "LINE_AA",
            "FONT_SCALE"     : 0.80,
            "FONT_THICKNESS" : 1,
            "FONT_OUTLINE"   : True
        },
        "ORB_PROPERTIES" : {
            "MODE"        : "ha",  # ha = hour angle, az = azimuth, alt = altitude, off = off
            "RADIUS"      : 9,
            "SUN_COLOR"   : [255, 255, 255],
            "MOON_COLOR"  : [128, 128, 128]
        },
        "FILETRANSFER" : {
            "CLASSNAME"              : "pycurl_sftp",  # pycurl_sftp, pycurl_ftps, pycurl_ftpes, paramiko_sftp, python_ftp, python_ftpes
            "HOST"                   : "",
            "PORT"                   : 0,
            "USERNAME"               : "",
            "PASSWORD"               : "",
            "PRIVATE_KEY"            : "",
            "PUBLIC_KEY"             : "",
            "TIMEOUT"                : 5.0,
            "CERT_BYPASS"            : True,
            "REMOTE_IMAGE_NAME"      : "image.{0}",
            "REMOTE_IMAGE_FOLDER"        : "allsky",
            "REMOTE_METADATA_NAME"       : "latest_metadata.json",
            "REMOTE_METADATA_FOLDER"     : "allsky",
            "REMOTE_VIDEO_FOLDER"        : "allsky/videos",
            "REMOTE_KEOGRAM_FOLDER"      : "allsky/keograms",
            "REMOTE_STARTRAIL_FOLDER"    : "allsky/startrails",
            "REMOTE_ENDOFNIGHT_FOLDER"   : "allsky",
            "UPLOAD_IMAGE"           : 0,
            "UPLOAD_METADATA"        : False,
            "UPLOAD_VIDEO"           : False,
            "UPLOAD_KEOGRAM"         : False,
            "UPLOAD_STARTRAIL"       : False,
            "UPLOAD_ENDOFNIGHT"      : False,
            "LIBCURL_OPTIONS"        : {}
        },
        "MQTTPUBLISH" : {
            "ENABLE"                 : False,
            "TRANSPORT"              : "tcp",  # tcp or websockets
            "HOST"                   : "localhost",
            "PORT"                   : 8883,  # 1883 = mqtt, 8883 = TLS
            "USERNAME"               : "indi-allsky",
            "PASSWORD"               : "",
            "BASE_TOPIC"             : "indi-allsky",
            "QOS"                    : 0,  # 0, 1, or 2
            "TLS"                    : True,
            "CERT_BYPASS"            : True
        },
        "LIBCAMERA" : {
            "IMAGE_FILE_TYPE"        : "dng",
            "EXTRA_OPTIONS"          : ""
        }
    }


    def __init__(self, config):
        self.config = self.template_config  # populate initial values
        self.config = config


    @property
    def template_config(self):
        return self._template_config

    @template_config.setter
    def template_config(self, new_template_config):
        pass  # read only


    @property
    def config(self):
        return self._config

    @config.setter
    def config(self, new_config):
        for k, v in new_config.items():
            setattr(self, k, v)

