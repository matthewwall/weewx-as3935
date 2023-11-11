# $Id: as3935.py 1560 2016-09-28 20:01:07Z mwall $
# Copyright 2015 Matthew Wall

"""
A service for weewx that reads the AS3935 lightning sensor range.  This service
will add two fields to each archive record:

  lightning_strikes - number of lightning strikes in the past interval
  avg_distance - average distance of the lightning strikes

To track these and use them in reports and plots, extend the weewx database
schema as described in the weewx customization guide.

The service can also save data to a separate 'lightning' database using the
data_binding option.

Configuration:

Rev. 1 Raspberry Pis should leave bus set at 0, while rev. 2 Pis should
set bus equal to 1. The address should be changed to match the address of
the sensor.

[AS3935]
    address = 3
    bus = 1
    noise_floor = 0
    calibration = 6
    indoors = True
    pin = 17

[Engine]
    [[Services]]
        data_services = ..., user.as3935.AS3935

Packet binding:

The service can be bound to LOOP packets or archive records.  Default is
archive records.  Use the binding parameter to specify.  For example, to
augment LOOP packets instead of the default, which is archive records:

[AS3935]
    ...
    binding = loop

Data binding:

You can record every lightning strike by saving lightning data to a separate
database.  To do this, then specify a data_binding for that database and the
associated DataBinding and Database entries in the weewx configuration file:

[AS3935]
    ...
    data_binding = lightning_binding

[DataBindings]
    [[lightning_binding]]
        database = lightning_sqlite
        table_name = archive
        manager = weewx.manager.DaySummaryManager
        schema = user.as3935.schema

[Databases]
    [[lightning_sqlite]]
        database_name = lightning.sdb
        database_type = SQLite

Credit:

Based on Phil Fenstermacher's RaspberryPi-AS3935 library and demo.py script.
  https://github.com/pcfens/RaspberryPi-AS3935
"""

from RPi_AS3935 import RPi_AS3935
import RPi.GPIO as GPIO
import time
import syslog
import weewx
import weewx.manager
from datetime import datetime
from weewx.wxengine import StdService
from weeutil.weeutil import to_bool

VERSION = "0.6"

if weewx.__version__ < "3.2":
    raise weewx.UnsupportedFeature("weewx 3 is required, found %s" %
                                   weewx.__version__)

# uncomment this if you want to sum counts instead of getting counts per period
#weewx.accum.extract_dict['lightning_strikes'] = weewx.accum.Accum.sum_extract

schema = [('dateTime', 'INTEGER NOT NULL PRIMARY KEY'),
          ('usUnits', 'INTEGER NOT NULL'),
          ('distance', 'REAL')]

def get_default_binding_dict():
    return {'database': 'lightning_sqlite',
            'manager': 'weewx.manager.Manager',
            'table_name': 'archive',
            'schema': 'user.as3935.schema'}

def logmsg(level, msg):
    syslog.syslog(level, 'as3935: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)

class AS3935(StdService):
    def __init__(self, engine, config_dict):
        super(AS3935, self).__init__(engine, config_dict)
        loginf("service version is %s" % VERSION)
        svc_dict = config_dict.get('AS3935', {})
        addr = int(svc_dict.get('address', 0x03))
        loginf("address=0x%02x" % addr)
        bus = int(svc_dict.get('bus', 1))
        loginf("bus=%s" % bus)
        indoors = to_bool(svc_dict.get('indoors', True))
        loginf("indoors=%s" % indoors)
        noise_floor = int(svc_dict.get('noise_floor', 0))
        loginf("noise_floor=%s" % noise_floor)
        calib = int(svc_dict.get('calibration', 0x6))
        loginf("calibration=0x%02x" % calib)
        self.pin = int(svc_dict.get('pin', 17))
        loginf("pin=%s" % self.pin)
        self.data_binding = svc_dict.get('data_binding', None)
        loginf("data_binding=%s" % self.data_binding)
        pkt_binding = svc_dict.get('binding', 'archive')
        loginf("binding=%s" % pkt_binding)

        self.data = []

        # if a binding was specified, then use it to save strikes to database
        if self.data_binding is not None:
            # configure the lightning database
            dbm_dict = weewx.manager.get_manager_dict(
                config_dict['DataBindings'], config_dict['Databases'],
                self.data_binding, default_binding_dict=get_default_binding_dict())
            with weewx.manager.open_manager(dbm_dict, initialize=True) as dbm:
                # ensure schema on disk matches schema in memory
                dbcol = dbm.connection.columnsOf(dbm.table_name)
                memcol = [x[0] for x in dbm_dict['schema']]
                if dbcol != memcol:
                    raise Exception('as3935: schema mismatch: %s != %s' %
                                    (dbcol, memcol))

        # configure the sensor
        self.sensor = RPi_AS3935.RPi_AS3935(address=addr, bus=bus)
        self.sensor.set_indoors(indoors)
        self.sensor.set_noise_floor(noise_floor)
        self.sensor.calibrate(tun_cap=calib)

        # configure the gpio
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pin, GPIO.IN)

        # add a gpio callback for the lightning strikes
        try:
            # be sure nothing is registered already...
            GPIO.remove_event_detect(self.pin)
        except:
            pass
        # ... then add our handler
        GPIO.add_event_detect(self.pin, GPIO.RISING,
                              callback=self.handle_interrupt)

        # on each new record, read then clear data since last record
        if pkt_binding.lower() == 'loop':
            self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)
        else:
            self.bind(weewx.NEW_ARCHIVE_RECORD, self.new_archive_record)

    def shutDown(self):
        GPIO.remove_event_detect(self.pin)
        GPIO.cleanup()

    def new_loop_packet(self, event):
        self.read_data(event.packet)

    def new_archive_record(self, event):
        self.read_data(event.record)

    def read_data(self, pkt):
        avg = None
        count = len(self.data)
        if count:
            avg = 0
            for x in self.data:
                avg += x[1]
            avg /= count
        # if the record is not metric, convert from kilometers to miles
        if 'usUnits' in pkt and pkt['usUnits'] == weewx.US:
            avg = weewx.units.convert((avg, 'km', 'group_distance'), 'mile')[0]
        # save the count and average
        pkt['avg_distance'] = avg
        pkt['lightning_strikes'] = count
        # clear the count and average
        self.data = []

    def save_data(self, strike_ts, distance):
        if self.data_binding is None:
            return
        rec = {'dateTime': strike_ts,
               'usUnits': weewx.METRIC,
               'distance': distance}
        dbm_dict = weewx.manager.get_manager_dict(
            self.config_dict['DataBindings'], self.config_dict['Databases'],
            self.data_binding, default_binding_dict=get_default_binding_dict())
        with weewx.manager.open_manager(dbm_dict) as dbm:
            dbm.addRecord(rec)

    def handle_interrupt(self, channel):
        try:   
            time.sleep(0.003)
            reason = self.sensor.get_interrupt()
            if reason == 0x01:
                loginf("noise level too high - adjusting")  
                self.sensor.raise_noise_floor()
            elif reason == 0x04:
                loginf("detected disturber - masking")
                self.sensor.set_mask_disturber(True)
            elif reason == 0x08:
                strike_ts = int(time.time())
                distance = float(self.sensor.get_distance())
                loginf("strike at %s km" % distance)
                self.data.append((strike_ts, distance))
                self.save_data(strike_ts, distance)
        except Exception as e:
            logerr("callback failed: %s" % e)
