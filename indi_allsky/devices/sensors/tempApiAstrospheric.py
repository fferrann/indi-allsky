import socket
import time
import json
import ssl
import requests
import logging

from .sensorBase import SensorBase
from ... import constants
from ..exceptions import SensorReadException


logger = logging.getLogger('indi_allsky')


class TempApiAstrospheric(SensorBase):

    URL = 'https://astrosphericpublicaccess.azurewebsites.net/api/GetForecastData_V1'


    METADATA = {
        'name' : 'Astrospheric API',
        'description' : 'Astrospheric API Sensor',
        'count' : 5,
        'labels' : (
            'Temperature',
            'Atmospheric Seeing',
            'Atmospheric Transparency',
            'Cloud Cover',
            'Wind Speed',
        ),
        'types' : (
            constants.SENSOR_TEMPERATURE,
            constants.SENSOR_MISC,
            constants.SENSOR_MISC,
            constants.SENSOR_MISC,
            constants.SENSOR_WIND_SPEED,
        ),
    }


    def __init__(self, *args, **kwargs):
        super(TempApiAstrospheric, self).__init__(*args, **kwargs)

        logger.warning('Initializing [%s] Astrospheric API Sensor', self.name)

        apikey = self.config.get('TEMP_SENSOR', {}).get('ASTROSPHERIC_APIKEY', '')

        if not apikey:
            raise Exception('Astrospheric API key is empty')


        latitude = self.config['LOCATION_LATITUDE']
        longitude = self.config['LOCATION_LONGITUDE']

        self.auth_data = {
            'Latitude'  : latitude,
            'Longitude' : longitude,
            'APIKey'    : apikey,
        }

        self.data = {
            'data' : tuple(),
        }

        self.next_run = time.time()  # run immediately
        self.next_run_offset = 1800  # 30 minutes


    def update(self):
        now = time.time()
        if now < self.next_run:
            # return cached data
            return self.data


        self.next_run = now + self.next_run_offset



        try:
            r = requests.post(
                self.URL,
                json=self.auth_data,
                headers={'Content-Type': 'application/json'},
                verify=True,
                timeout=(5.0, 10.0),
            )
        except socket.gaierror as e:
            raise SensorReadException(str(e)) from e
        except socket.timeout as e:
            raise SensorReadException(str(e)) from e
        except requests.exceptions.ConnectTimeout as e:
            raise SensorReadException(str(e)) from e
        except requests.exceptions.ConnectionError as e:
            raise SensorReadException(str(e)) from e
        except requests.exceptions.ReadTimeout as e:
            raise SensorReadException(str(e)) from e
        except ssl.SSLCertVerificationError as e:
            raise SensorReadException(str(e)) from e
        except requests.exceptions.SSLError as e:
            raise SensorReadException(str(e)) from e



        if r.status_code >= 400:
            raise SensorReadException('Astrospheric API returned {0:d}'.format(r.status_code))


        try:
            r_data = r.json()
        except json.JSONDecodeError as e:
            raise SensorReadException(str(e)) from e


        temp_k = float(r_data['RDPS_Temperature'][0]['Value']['ActualValue'])
        dew_point_k = float(r_data['RDPS_DewPoint'][0]['Value']['ActualValue'])
        seeing = float(r_data['Astrospheric_Seeing'][0]['Value']['ActualValue'])
        transparency = float(r_data['Astrospheric_Transparency'][0]['Value']['ActualValue'])
        clouds_percent = float(r_data['RDPS_CloudCover'][0]['Value']['ActualValue'])
        wind_speed = float(r_data['RDPS_WindVelocity'][0]['Value']['ActualValue'])
        wind_deg = float(r_data['RDPS_WindDirection'][0]['Value']['ActualValue'])


        logger.info('[%s] Astrospheric API - temp: %0.1fk, dew point: %0.1fk, clouds: %0.1f%%', self.name, temp_k, dew_point_k, clouds_percent)


        temp_c = self.k2c(temp_k)
        dew_point_c = self.k2c(temp_k)


        try:
            frost_point_c = self.get_frost_point_c(temp_c, dew_point_c)
        except ValueError as e:
            logger.error('Frost Point calculation error - ValueError: %s', str(e))
            frost_point_c = 0.0


        if self.config.get('TEMP_DISPLAY') == 'f':
            current_temp = self.c2f(temp_c)
            current_dp = self.c2f(dew_point_c)
        elif self.config.get('TEMP_DISPLAY') == 'k':
            current_temp = temp_k
            current_dp = temp_k
            current_fp = self.c2k(frost_point_c)
        else:
            current_temp = temp_c
            current_dp = dew_point_c
            current_fp = frost_point_c


        if self.config.get('WINDSPEED_DISPLAY') == 'mph':
            current_wind_speed = self.mps2miph(wind_speed)
        elif self.config.get('WINDSPEED_DISPLAY') == 'knots':
            current_wind_speed = self.mps2knots(wind_speed)
        elif self.config.get('WINDSPEED_DISPLAY') == 'kph':
            current_wind_speed = self.mps2kmph(wind_speed)
        else:
            # ms meters/s
            current_wind_speed = wind_speed


        self.data = {
            'dew_point' : current_dp,
            'frost_point' : current_fp,
            'wind_degrees' : wind_deg,
            'data' : (
                current_temp,
                seeing,
                transparency,
                clouds_percent,
                current_wind_speed,
            ),
        }

        return self.data


