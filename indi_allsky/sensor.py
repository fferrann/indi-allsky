import time
import traceback
import logging

from threading import Thread
#import queue
import threading

from .devices import dew_heaters
from .devices import temp_sensors
from .devices.exceptions import TemperatureReadException

logger = logging.getLogger('indi_allsky')


class SensorWorker(Thread):
    def __init__(
        self,
        idx,
        config,
        error_q,
        sensors_user_av,
        night_v,
    ):
        super(SensorWorker, self).__init__()

        self.name = 'Sensor-{0:d}'.format(idx)

        self.config = config
        self.error_q = error_q

        self.sensors_user_av = sensors_user_av
        self.night_v = night_v
        self.night = False

        self.dew_heater = None
        self.temp_sensors = [None, None]

        self.next_run = time.time()  # run immediately
        self.next_run_offset = 59

        self.temp_user_slot = self.config.get('DEW_HEATER', {}).get('TEMP_USER_VAR_SLOT', 10)

        self.level_default = self.set_dew_heater(self.config.get('DEW_HEATER', {}).get('LEVEL_DEF', 100))
        self.level_low = self.config.get('DEW_HEATER', {}).get('LEVEL_LOW', 33)
        self.level_med = self.config.get('DEW_HEATER', {}).get('LEVEL_MED', 66)
        self.level_high = self.config.get('DEW_HEATER', {}).get('LEVEL_HIGH', 100)

        self.thold_diff_low = self.config.get('DEW_HEATER', {}).get('THOLD_DIFF_LOW', 15)
        self.thold_diff_med = self.config.get('DEW_HEATER', {}).get('THOLD_DIFF_MED', 10)
        self.thold_diff_high = self.config.get('DEW_HEATER', {}).get('THOLD_DIFF_HIGH', 5)


        self._stopper = threading.Event()


    def stop(self):
        self._stopper.set()


    def stopped(self):
        return self._stopper.isSet()


    def run(self):
        # setup signal handling after detaching from the main process
        #signal.signal(signal.SIGHUP, self.sighup_handler_worker)
        #signal.signal(signal.SIGTERM, self.sigterm_handler_worker)
        #signal.signal(signal.SIGINT, self.sigint_handler_worker)
        #signal.signal(signal.SIGALRM, self.sigalarm_handler_worker)


        ### use this as a method to log uncaught exceptions
        try:
            self.saferun()
        except Exception as e:
            tb = traceback.format_exc()
            self.error_q.put((str(e), tb))
            raise e


    def saferun(self):
        #raise Exception('Test exception handling in worker')


        self.init_dew_heater()
        self.init_temp_sensors()


        while True:
            time.sleep(3)

            if self.stopped():
                logger.warning('Goodbye')
                return


            now = time.time()
            if not now >= self.next_run:
                continue


            # set next run
            self.next_run = now + self.next_run_offset

            #############################
            ### do interesting stuff here
            #############################


            if self.night != bool(self.night_v.value):
                self.night = bool(self.night_v.value)
                self.night_day_change()


            if self.sensors_user_av[2]:
                logger.info('Dew Point: %0.1f, Frost Point: %0.1f', self.sensors_user_av[2], self.sensors_user_av[3])


            # update temp sensor readings
            for temp_sensor in self.temp_sensors:
                try:
                    temp_data = temp_sensor.update()

                    with self.sensors_user_av.get_lock():
                        if temp_data['dew_point']:
                            self.sensors_user_av[2] = temp_data['dew_point']

                        if temp_data['frost_point']:
                            self.sensors_user_av[3] = temp_data['frost_point']

                        for i, v in enumerate(temp_data['data']):
                            self.sensors_user_av[temp_sensor.slot + i] = float(v)
                except TemperatureReadException as e:
                    logger.error('TemperatureReadException: {0:s}'.format(str(e)))


            # threshold processing
            if not self.night and self.config.get('DEW_HEATER', {}).get('ENABLE_DAY'):
                # daytime
                if self.set_dew_heater(self.config.get('DEW_HEATER', {}).get('THOLD_ENABLE')):
                    self.check_dew_heater_thresholds()
            else:
                # night
                if self.set_dew_heater(self.config.get('DEW_HEATER', {}).get('THOLD_ENABLE')):
                    self.check_dew_heater_thresholds()


    def night_day_change(self):
        # changing modes here
        if self.night:
            # night time
            if not self.dew_heater.state:
                self.set_dew_heater(self.level_default)

        else:
            # day time
            if self.config.get('DEW_HEATER', {}).get('ENABLE_DAY'):
                if not self.dew_heater.state:
                    self.set_dew_heater(self.level_default)
            else:
                self.set_dew_heater(0)


    def init_dew_heater(self):
        dew_heater_classname = self.config.get('DEW_HEATER', {}).get('CLASSNAME')
        if dew_heater_classname:
            dh = getattr(dew_heaters, dew_heater_classname)

            dh_pin_1 = self.config.get('DEW_HEATER', {}).get('PIN_1', 'notdefined')

            self.dew_heater = dh(self.config, pin_1_name=dh_pin_1)

            if self.night_v.value:
                self.set_dew_heater(self.level_default)
            else:
                if self.config.get('DEW_HEATER', {}).get('ENABLE_DAY'):
                    self.set_dew_heater(self.level_default)
                else:
                    self.set_dew_heater(0)


        else:
            self.dew_heater = dew_heaters.dew_heater_simulator(self.config)



    def set_dew_heater(self, new_state):
        if self.dew_heater.state != new_state:
            self.dew_heater.state = new_state

            with self.sensors_user_av.get_lock():
                self.sensors_user_av[1] = float(self.dew_heater.state)


    def init_temp_sensors(self):
        ### Sensor A
        a_temp_sensor_classname = self.config.get('TEMP_SENSOR', {}).get('A_CLASSNAME')
        if a_temp_sensor_classname:
            a_ts = getattr(temp_sensors, a_temp_sensor_classname)

            a_ts_i2c_address = self.config.get('TEMP_SENSOR', {}).get('A_I2C_ADDRESS', '0x77')
            a_ts_pin_1_name = self.config.get('TEMP_SENSOR', {}).get('A_PIN_1', 'notdefined')

            self.temp_sensors[0] = a_ts(self.config, pin_1_name=a_ts_pin_1_name, i2c_address=a_ts_i2c_address)
        else:
            self.temp_sensors[0] = temp_sensors.temp_sensor_simulator(self.config)

        self.temp_sensors[0].slot = self.config.get('TEMP_SENSOR', {}).get('A_USER_VAR_SLOT', 10)


        ### Sensor B
        b_temp_sensor_classname = self.config.get('TEMP_SENSOR', {}).get('B_CLASSNAME')
        if b_temp_sensor_classname:
            b_ts = getattr(temp_sensors, b_temp_sensor_classname)

            b_ts_i2c_address = self.config.get('TEMP_SENSOR', {}).get('B_I2C_ADDRESS', '0x76')
            b_ts_pin_1_name = self.config.get('TEMP_SENSOR', {}).get('B_PIN_1', 'notdefined')

            self.temp_sensors[1] = b_ts(self.config, pin_1_name=b_ts_pin_1_name, i2c_address=b_ts_i2c_address)
        else:
            self.temp_sensors[1] = temp_sensors.temp_sensor_simulator(self.config)

        self.temp_sensors[1].slot = self.config.get('TEMP_SENSOR', {}).get('B_USER_VAR_SLOT', 15)


    def check_dew_heater_thresholds(self):
        manual_target = self.config.get('DEW_HEATER', {}).get('MANUAL_TARGET', 0.0)
        if manual_target:
            target_val = manual_target
        else:
            target_val = self.sensors_user_av[2]  # dew point


        if not target_val:
            logger.warning('Dew heater target dew point is 0, possible misconfiguration')


        current_temp = self.sensors_user_av[self.temp_user_slot]  # dew point


        temp_diff = current_temp - target_val
        if temp_diff <= self.thold_diff_high:
            # set dew heater to high
            self.set_dew_heater(self.level_high)
        elif temp_diff <= self.thold_diff_med:
            # set dew heater to medium
            self.set_dew_heater(self.level_med)
        elif temp_diff <= self.thold_diff_low:
            # set dew heater to low
            self.set_dew_heater(self.level_low)
        else:
            self.set_dew_heater(self.level_default)
            #self.set_dew_heater(0)

