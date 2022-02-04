# coding: utf-8
#
#    Project: BioCAT user beamline control software (BioCON)
#             https://github.com/biocatiit/beamline-control-user
#
#
#    Principal author:       Jesse Hopkins
#
#    This is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This software is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this software.  If not, see <http://www.gnu.org/licenses/>.
from __future__ import absolute_import, division, print_function, unicode_literals
from builtins import object, range, map
from io import open

import traceback
import threading
import time
import collections
from collections import OrderedDict, deque
import queue
import logging
import sys
import copy
import platform

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import serial
import serial.tools.list_ports as list_ports
import wx
from six import string_types

print_lock = threading.RLock()

class SerialComm(object):
    """
    This class impliments a generic serial communication setup. The goal is
    to provide a lightweight wrapper around a pyserial Serial device to make sure
    ports are properly opened and closed whenever used.
    """
    def __init__(self, port=None, baudrate=9600, bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=None,
        xonxoff=False, rtscts=False, write_timeout=None, dsrdtr=False,
        inter_byte_timeout=None, exclusive=None):
        """
        Parameters are all of those accepted by a
        `pyserial.Serial <https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial>`_
        device, defaults are set to those default values.
        """
        self.ser = None

        logger.info("Attempting to connect to serial device on port %s", port)

        try:
            self.ser = serial.Serial(port, baudrate, bytesize, parity, stopbits, timeout,
                xonxoff, rtscts, write_timeout, dsrdtr, inter_byte_timeout, exclusive)
            logger.info("Connected to serial device on port %s", port)
        except ValueError:
            logger.exception("Failed to connect to serial device on port %s", port)
        except serial.SerialException:
            logger.exception("Failed to connect to serial device on port %s", port)
        finally:
            if self.ser is not None:
                self.ser.close()

    def __repr__(self):
        return self.ser

    def __str__(self):
        return print(self.ser)

    def read(self, size=1):
        """
        This wraps the Serial.read() function for reading in a specified
        number of bytes. It automatically decodes the return value.

        :param size: Number of bytes to read.
        :type size: int

        :returns: The ascii (decoded) value of the ``Serial.read()``
        :rtype: str
        """
        with self.ser as s:
            ret = s.read(size)

        logger.debug("Read %i bytes from serial device on port %s", size, self.ser.port)
        logger.debug("Serial device on port %s returned %s", self.ser.port, ret.decode('utf-8'))

        return ret.decode('utf-8')

    def read_all(self):
        """
        This wraps the Serial.read() function, and returns all of the
        waiting bytes.

        :returns: The ascii (decoded) value of the ``Serial.read()``
        :rtype: str
        """
        with self.ser as s:
            ret = s.read(s.in_waiting)

        logger.debug("Read all waiting bytes from serial device on port %s", self.ser.port)
        logger.debug("Serial device on port %s returned %s", self.ser.port, ret.decode('utf-8'))

        return ret.decode('utf-8')

    def write(self, data, get_response=False, send_term_char = '\r\n', term_char='>'):
        """
        This warps the Serial.write() function. It encodes the input
        data if necessary. It can return any expected response from the
        controller.

        :param data: Data to be written to the serial device.
        :type data: str, bytes

        :param term_char: The terminal character expected in a response
        :type term_char: str

        :returns: The requested response, or an empty string
        :rtype: str
        """
        logger.debug("Sending '%s' to serial device on port %s", data, self.ser.port)
        if isinstance(data, string_types):
            if not data.endswith(send_term_char):
                data += send_term_char
            data = data.encode()

        out = ''
        try:
            with self.ser as s:
                s.write(data)
                if get_response:
                    while not out.endswith(term_char):
                        if s.in_waiting > 0:
                            ret = s.read(s.in_waiting)
                            out += ret.decode('ascii')

                        time.sleep(.001)
        except ValueError:
            logger.exception("Failed to write '%s' to serial device on port %s", data, self.ser.port)

        logger.debug("Recived '%s' after writing to serial device on port %s", out, self.ser.port)

        return out

class MForceSerialComm(SerialComm):
    """
    This class subclases ``SerialComm`` to handle MForce specific
    errors.
    """

    def write(self, data, get_response=True, term_char='>'):
        """
        This warps the Serial.write() function. It encodes the input
        data if necessary. It can return any expected response from the
        controller.

        :param data: Data to be written to the serial device.
        :type data: str, bytes

        :param term_char: The terminal character expected in a response
        :type term_char: str

        :returns: The requested response, or an empty string
        :rtype: str
        """
        logger.debug("Sending %r to serial device on port %s", data, self.ser.port)
        if isinstance(data, str):
            if not data.endswith('\r\n'):
                data += '\r\n'
            data = data.encode()

        out = ''
        timeout = 1
        start_time = time.time()
        try:
            with self.ser as s:
                s.write(data)
                if get_response:
                    while not out.strip().endswith(term_char) and time.time()-start_time<timeout:
                        if s.in_waiting > 0:
                            ret = s.read(s.in_waiting)
                            out += ret.decode('ascii')

                        if out.strip().endswith('?'):
                            s.write('PR ER\r\n'.encode())
                            out = ''

                        time.sleep(.001)
        except ValueError:
            logger.exception("Failed to write %r to serial device on port %s", data, self.ser.port)

        logger.debug("Recived %r after writing to serial device on port %s", out, self.ser.port)

        return out

class PHD4400SerialComm(SerialComm):
    """
    This class subclases ``SerialComm`` to handle PHD4400 specific
    quirks.
    """

    def write(self, data, pump_address, get_response=False, send_term_char = '\r',
        term_chars=':></*^'):
        """
        This warps the Serial.write() function. It encodes the input
        data if necessary. It can return any expected response from the
        controller.

        :param data: Data to be written to the serial device.
        :type data: str, bytes

        :param term_char: The terminal character expected in a response
        :type term_char: str

        :returns: The requested response, or an empty string
        :rtype: str
        """
        logger.debug("Sending '%s' to serial device on port %s", data, self.ser.port)
        if isinstance(data, string_types):
            if not data.endswith(send_term_char):
                data += send_term_char
            data = data.encode()

        out = ''

        possible_term = ['\n{}{}'.format(pump_address, char) for char in term_chars]
        try:
            with self.ser as s:
                s.write(data)
                if get_response:
                    got_resp = False
                    while not got_resp:
                        if s.in_waiting > 0:
                            ret = s.read(s.in_waiting)
                            out += ret.decode('ascii')
                            # logger.debug(out)
                            for term in possible_term:
                                if out.endswith(term):
                                    got_resp = True
                                    break

                        time.sleep(.001)
        except ValueError:
            logger.exception("Failed to write '%s' to serial device on port %s", data, self.ser.port)
        except Exception:
            logger.error("Failed to write to serial port!")
        logger.debug("Recived '%s' after writing to serial device on port %s", out, self.ser.port)

        return out

class Pump(object):
    """
    This class contains the settings and communication for a generic pump.
    It is intended to be subclassed by other pump classes, which contain
    specific information for communicating with a given pump. A pump object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, device, name):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """

        self.device = device
        self.name = name

    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    def __str__(self):
        return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    @property
    def flow_rate(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        :type: float
        """
        pass #Should be implimented in each subclass

    @flow_rate.setter
    def flow_rate(self, rate):
        pass #Should be implimented in each subclass

    @property
    def units(self):
        """
        Sets and returns the pump flow rate units. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :type: str
        """
        return self._units

    @units.setter
    def units(self, units):
        old_units = self._units
        flow_rate = self.flow_rate

        if units in ['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min']:
            self._units = units
            old_vu, old_tu = old_units.split('/')
            new_vu, new_tu = self._units.split('/')[0]
            if old_vu != new_vu:
                if (old_vu == 'nL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'mL'):
                    flow_rate = flow_rate/1000.
                elif old_vu == 'nL' and new_vu == 'mL':
                    flow_rate = flow_rate/1000000.
                elif (old_vu == 'mL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'nL'):
                    flow_rate = flow_rate*1000.
                elif old_vu == 'mL' and new_vu == 'nL':
                    flow_rate = flow_rate*1000000.
            if old_tu != new_tu:
                if old_tu == 'min':
                    flow_rate = flow_rate/60
                else:
                    flow_rate = flow_rate*60

            logger.info("Changed pump %s units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change pump %s units, units supplied were invalid: %s", self.name, units)


    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.

        :param get_response: Whether the program should get a response from the pump
        :type get_response: bool
        """
        pass #Should be implimented in each subclass


    def is_moving(self):
        """
        Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        pass #Should be implimented in each subclass

    def start_flow(self):
        """
        Starts a continuous flow at the flow rate specified by the
        ``Pump.flow_rate`` variable.
        """
        pass #Should be implimented in each subclass

    def dispense(self, vol, units='uL'):
        """
        Dispenses a fixed volume.

        :param vol: Volume to dispense
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        pass #Should be implimented in each subclass

    def aspirate(self, vol, units='uL'):
        """
        Aspirates a fixed volume.

        :param vol: Volume to aspirate
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        pass #Should be implimented in each subclass

    def stop(self):
        """Stops all pump flow."""
        pass #Should be implimented in each subclass

    def disconnect(self):
        """Close any communication connections"""
        pass #Should be implimented in each subclass

class M50Pump(Pump):
    """
    .. todo:: This class doesn't know when the pump is done dispensing. This leads
        to unncessary stop signals being sent to the pump, and makes the log harder
        to follow. This could be fixed, when I have time.

    This class contains information for initializing and communicating with
    a VICI M50 Pump using an MForce Controller. Below is an example that
    initializes an M50 pump, starts a flow of 2000 uL/min, and then stops the flow. ::

        >>> my_pump = M50Pump('COM6', 'pump2', flow_cal=626.2, backlash_cal=9.278)
        >>> my_pump.flow_rate = 2000
        >>> my_pump.start_flow()
        >>> my_pump.stop_flow()
    """

    def __init__(self, device, name, comm_lock=threading.Lock(), flow_cal=628., backlash_cal=1.5):
        """
        This makes the initial serial connection, and then sets the MForce
        controller parameters to the correct values.

        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str

        :param flow_cal: The pump-specific flow calibration, in uL/rev. Defaults to 628 uL/rev
        :type flow_cal: float

        :param backlash_cal: The pump-specific backlash calibration, in uL. Default to 1.5 uL
        :type backlash_cal: float
        """
        Pump.__init__(self, device, name)

        logstr = ("Initializing pump {} on serial port {}, flow "
            "calibration: {} uL/rev, backlash calibration: {} uL".format(self.name,
            self.device, flow_cal, backlash_cal))
        logger.info(logstr)

        self.comm_lock = comm_lock

        self.comm_lock.acquire()
        self.pump_comm = MForceSerialComm(device)
        self.comm_lock.release()


        #Make sure parameters are set right
        self.send_cmd('EM 0') #Echo mode to full duplex
        self.send_cmd('MS 256') #Microstepping to 256, MForce default
        self.send_cmd('VI 1000') #Initial velocity to 1000, MForce default
        self.send_cmd('A 1000000') #Acceleration to 1000000, MForce default
        self.send_cmd('D 1000000') #Deceleration to 1000000, MForce default
        self.send_cmd('HC 5') #Hold current to 5%, MForce default
        self.send_cmd('RC 25') #Run current to 25%, MForce default
        # Next command doesn't match syntax in manual. Manual says it should be S1=17,1,0
        self.send_cmd('S1 17,0,0') #Sets output 1 to be active high (sinking) when motor is moving
        # # self.send_cmd('S') #Saves current settings in non-volatile memory

        self._is_flowing = False
        self._is_dispensing = False

        self._units = 'uL/min'
        self._flow_rate = 0
        self._flow_dir = 0

        self._flow_cal = flow_cal
        self._backlash_cal = backlash_cal
        self.gear_ratio = 9.88 #Gear ratio provided by manufacturer, for M50 pumps

        self.cal = 200*256*self.gear_ratio/self._flow_cal #Calibration value in (micro)steps/useful
            #full steps/rev * microsteps/full step * gear ratio / uL/revolution = microsteps/uL

    @property
    def flow_rate(self):
        rate = self._flow_rate/self.cal

        if self.units.split('/')[1] == 'min':
            rate = rate*60.

        if self.units.split('/')[0] == 'mL':
            rate = rate/1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate*1000

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s flow rate to %f %s", self.name, rate, self.units)

        if self.units.split('/')[0] == 'mL':
            rate = rate*1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate/1000.

        if self.units.split('/')[1] == 'min':
            rate = rate/60.

        #Maximum continuous flow rate is 25 mL/min
        if rate>25000/60.:
            rate = 25000/60.
            logger.warning("Requested flow rate > 25 mL/min, setting pump %s flow rate to 25 mL/min", self.name)
        elif rate<-25000/60.:
            rate = -25000/60.
            logger.warning("Requested flow rate > 25 mL/min, setting pump %s flow rate to -25 mL/min", self.name)

        #Minimum flow rate is 1 uL/min
        if abs(rate) < 1/60. and rate != 0:
            if rate>0:
                logger.warning("Requested flow rate < 1 uL/min, setting pump %s flow rate to 1 uL/min", self.name)
                rate = 1/60.
            else:
                logger.warning("Requested flow rate < 1 uL/min, setting pump %s flow rate to -1 uL/min", self.name)
                rate = -1/60.


        self._flow_rate = int(round(rate*self.cal))

        if self._is_flowing:
            self.send_cmd("SL {}".format(self._flow_rate))
        elif self._is_dispensing:
            self.send_cmd("VM {}".format(abs(self._flow_rate)))


    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.
        :type cmd: str, bytes

        :param get_response: Whether the program should get a response from the pump
        :type get_response: bool
        """
        logger.debug("Sending pump %s cmd %r", self.name, cmd)

        self.comm_lock.acquire()
        ret = self.pump_comm.write(cmd, get_response)
        self.comm_lock.release()

        if get_response:
            logger.debug("Pump %s returned %r", self.name, ret)

        return ret


    def is_moving(self):
        status = self.send_cmd("PR MV")

        status = status.split('\r\n')[-2][-1]
        status = bool(int(status))

        logger.debug("Pump %s moving: %s", self.name, str(status))

        return status

    def start_flow(self):
        if self._is_flowing or self._is_dispensing:
            logger.debug("Stopping pump %s current motion before starting continuous flow", self.name)
            self.stop()

        logger.info("Pump %s starting continuous flow at %f %s", self.name, self.flow_rate, self.units)
        self.send_cmd("SL {}".format(self._flow_rate))

        self._is_flowing = True
        if self._flow_rate > 0:
            self._flow_dir = 1
        elif self._flow_rate < 0:
            self._flow_dir = -1

    def dispense(self, vol, units='uL'):
        if self._is_flowing or self._is_dispensing:
            logger.debug("Stopping pump %s current motion before starting flow", self.name)
            self.stop()

        if vol > 0:
            logger.info("Pump %s dispensing %f %s at %f %s", self.name, vol, units, self.flow_rate, self.units)
        elif vol < 0:
            logger.info("Pump %s aspirating %f %s at %f %s", self.name, abs(vol), units, self.flow_rate, self.units)

        if units == 'mL':
            vol = vol*1000.
        elif units == 'nL':
            vol = vol/1000.

        if vol > 0 and self._flow_dir < 0:
            vol = vol + self._backlash_cal
            logger.debug("Pump %s added backlash correction for dispensing/aspirating", self.name)
        elif vol < 0 and self._flow_dir > 0:
            vol = vol - self._backlash_cal
            logger.debug("Pump %s added backlash correction for dispensing/aspirating", self.name)

        vol =int(round(vol*self.cal))

        self.send_cmd("VM {}".format(abs(self._flow_rate)))
        self.send_cmd("MR {}".format(vol))

        self._is_dispensing = True
        if vol > 0:
            self._flow_dir = 1
        elif vol < 0:
            self._flow_dir = -1

    def aspirate(self, vol, units='uL'):
        self.dispense(-1*vol, units)

    def stop(self):
        logger.info("Pump %s stopping all motions", self.name)
        self.send_cmd("SL 0")
        self.send_cmd("\x1B")
        self._is_flowing = False
        self._is_dispensing = False

    def disconnect(self):
        logger.debug("Closing pump %s serial connection", self.name)
        self.pump_comm.ser.close()

class PHD4400Pump(Pump):
    """
    This class contains the settings and communication for a generic pump.
    It is intended to be subclassed by other pump classes, which contain
    specific information for communicating with a given pump. A pump object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, device, name, pump_address, diameter, max_volume, max_rate,
        syringe_id, dual_syringe, comm_lock):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """

        Pump.__init__(self, device, name)

        logstr = ("Initializing PHD4400 pump {} on serial port {}".format(name, device))
        logger.info(logstr)

        self.comm_lock = comm_lock

        self.comm_lock.acquire()
        self.pump_comm = PHD4400SerialComm(device, stopbits=serial.STOPBITS_TWO,
            baudrate=19200)
        self.comm_lock.release()

        self._is_flowing = False
        self._is_dispensing = False

        self._units = 'mL/min'
        self._flow_rate = 0
        self._refill_rate = 0
        self._flow_dir = 0

        self._volume = 0

        self._pump_address = pump_address

        self.dual_syringe = dual_syringe

        self.stop()
        self.set_pump_cal(diameter, max_volume, max_rate, syringe_id)
        self.send_cmd('MOD VOL')


    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    def __str__(self):
        return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    @property
    def flow_rate(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        Pump _flow_rate variable should always be stored in ml/min.

        For these pumps, the flow_rate variable is considered to be the infuse rate,
        whereas the refill_rate variable is the refill rate.

        :type: float
        """
        rate = self._flow_rate

        if self.units.split('/')[1] == 's':
            rate = rate/60.

        if self.units.split('/')[0] == 'uL':
            rate = rate*1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate*1.e6

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s infuse flow rate to %f %s", self.name, rate, self.units)

        if self.units.split('/')[0] == 'uL':
            rate = rate/1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate/1.e6

        if self.units.split('/')[1] == 's':
            rate = rate*60.

        self._flow_rate = self.round(rate)

        #Have to do this or can lose aspirate/dispense volume
        volume = self._volume

        if self._is_dispensing and not self.is_moving():
            vol = self.get_delivered_volume()

            if self._flow_dir > 0:
                volume = volume - vol
            elif self._flow_dir < 0:
                volume = volume + vol

        self._volume = volume

        self.send_cmd("RAT {} MM".format(self._flow_rate))

    @property
    def refill_rate(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        Pump _refill_rate variable should always be stored in ml/min.

        For these pumps, the refill_rate variable is considered to be the infuse rate,
        whereas the refill_rate variable is the refill rate.

        :type: float
        """
        rate = self._refill_rate

        if self.units.split('/')[1] == 's':
            rate = rate/60.

        if self.units.split('/')[0] == 'uL':
            rate = rate*1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate*1.e6

        return rate

    @refill_rate.setter
    def refill_rate(self, rate):
        logger.info("Setting pump %s refill flow rate to %f %s", self.name, rate, self.units)

        if self.units.split('/')[0] == 'uL':
            rate = rate/1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate/1.e6

        if self.units.split('/')[1] == 's':
            rate = rate*60.

        self._refill_rate = self.round(rate)
        logger.info('Checking volume')

        #Have to do this or can lose aspirate/dispense volume
        volume = self._volume

        if self._is_dispensing and not self.is_moving():
            vol = self.get_delivered_volume()

            if self._flow_dir > 0:
                volume = volume - vol
            elif self._flow_dir < 0:
                volume = volume + vol

        self._volume = volume

        self.send_cmd("RFR {} MM".format(self._refill_rate))

    @property
    def volume(self):
        volume = self._volume

        if self._is_dispensing:
            vol = self.get_delivered_volume()

            if self._flow_dir > 0:
                volume = volume - vol
            elif self._flow_dir < 0:
                volume = volume + vol

        return volume

    @volume.setter
    def volume(self, volume):

        if self._is_dispensing:
            vol = self.get_delivered_volume()

            if self._flow_dir > 0:
                volume = volume + vol
            elif self._flow_dir < 0:
                volume = volume - vol

        self._volume = volume

    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.
        """

        logger.debug("Sending pump %s cmd %r", self.name, cmd)

        self.comm_lock.acquire()

        ret = self.pump_comm.write("{}{}".format(self._pump_address, cmd),
            self._pump_address, get_response=get_response, send_term_char='\r')

        time.sleep(0.01)
        self.comm_lock.release()

        logger.debug("Pump %s returned %r", self.name, ret)

        return ret

    def is_moving(self):
        """
        Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        ret = self.send_cmd("")

        if ret.endswith('>') or ret.endswith('<'):
            moving = True
        else:
            moving = False

        return moving

    def get_delivered_volume(self):
        ret = self.send_cmd("DEL")

        vol = float(ret.split('\n')[1].strip())

        return vol

    def dispense_all(self, blocking=True):
        if self._is_flowing or self._is_dispensing:
            logger.debug("Stopping pump %s current motion before infusing", self.name)
            self.stop()

        self.dispense(self.volume, blocking=blocking)

    def dispense(self, vol, units='mL', blocking=True):
        """
        Dispenses a fixed volume.

        :param vol: Volume to dispense
        :type vol: float

        :param units: Volume units, defaults to mL, also accepts uL or nL
        :type units: str
        """
        if units == 'uL':
            vol = vol/1000.
        elif units == 'nL':
            vol = vol/1e6

        if self._is_flowing or self._is_dispensing:
            logger.debug("Stopping pump %s current motion before infusing", self.name)
            self.stop()

        cont = True

        if self.volume - vol < 0:
            logger.error(("Attempting to infuse {} mL, which is more than the "
                "current volume of the syringe ({} mL)".format(vol, self.volume)))
            cont = False

        if vol <= 0:
            logger.error(("Infuse volume must be positive."))
            cont = False

        if cont:
            vol = self.round(vol)

            logger.info("Pump %s infusing %f %s at %f %s", self.name, vol, units, self.flow_rate, self.units)

            self.send_cmd("DIR INF")
            self.send_cmd("CLD")
            self.send_cmd("TGT {}".format(vol))
            self.send_cmd("RUN")

            self._is_dispensing = True
            self._flow_dir = 1

    def aspirate_all(self):
        if self._is_flowing or self._is_dispensing:
            logger.debug("Stopping pump %s current motion before aspirating", self.name)
            self.stop()

        if self.max_volume - self.volume > 0:
            self.aspirate(self.max_volume - self.volume)
        else:
            logger.error(("Already at maximum volume, can't aspirate more."))

    def aspirate(self, vol, units='mL'):
        """
        Aspirates a fixed volume.

        :param vol: Volume to aspirate
        :type vol: float

        :param units: Volume units, defaults to mL, also accepts uL or nL
        :type units: str
        """

        if units == 'uL':
            vol = vol/1000.
        elif units == 'nL':
            vol = vol/1e6

        if self._is_flowing or self._is_dispensing:
            logger.debug("Stopping pump %s current motion before refilling", self.name)
            self.stop()

        cont = True

        if self.volume + vol > self.max_volume:
            logger.error(("Attempting to refill {} mL, which will take the total "
                "loaded volume to more than the maximum volume of the syringe "
                "({} mL)".format(vol, self.max_volume)))
            cont = False

        if vol <= 0:
            logger.error(("Refill volume must be positive."))
            cont = False

        if cont:
            vol = self.round(vol)

            logger.info("Pump %s refilling %f %s at %f %s", self.name, vol, units, self.refill_rate, self.units)

            self.send_cmd("DIR REF")
            self.send_cmd("CLD")
            self.send_cmd("TGT {}".format(vol))
            self.send_cmd("RUN")

            self._is_dispensing = True
            self._flow_dir = -1

    def stop(self):
        """Stops all pump flow."""
        logger.info("Pump %s stopping all motions", self.name)
        self.send_cmd("STP")

        if self._is_dispensing:
            vol = self.get_delivered_volume()

            if self._flow_dir > 0:
                self._volume = self._volume - vol
            elif self._flow_dir < 0:
                self._volume = self._volume + vol

        self._is_dispensing = False
        self._is_flowing = False
        self._flow_dir = 0

    def set_pump_cal(self, diameter, max_volume, max_rate, syringe_id):
        self.diameter = diameter
        self.max_volume = max_volume
        self.max_rate = max_rate
        self.syringe_id = syringe_id

        self.send_cmd("DIA {}".format(self.diameter))

    def round(self, val):
        oom = int('{:e}'.format(val).split('e')[1])

        if oom < 0:
            oom = 0

        num_dig = 6-(oom + 2)

        return round(val, num_dig)

    def disconnect(self):
        """Close any communication connections"""
        logger.debug("Closing pump %s serial connection", self.name)
        self.pump_comm.ser.close()


class NE500Pump(Pump):
    """
    This class contains the settings and communication for a generic pump.
    It is intended to be subclassed by other pump classes, which contain
    specific information for communicating with a given pump. A pump object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, device, name, pump_address, diameter, max_volume, max_rate,
        syringe_id, dual_syringe, comm_lock):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """

        Pump.__init__(self, device, name)

        logstr = ("Initializing NE500 pump {} on serial port {}".format(name, device))
        logger.info(logstr)

        self.comm_lock = comm_lock

        self.comm_lock.acquire()
        self.pump_comm = SerialComm(device, baudrate=19200)
        self.comm_lock.release()

        self._is_flowing = False
        self._is_dispensing = False

        self._units = 'mL/min'
        self._flow_rate = 0
        self._refill_rate = 0
        self._flow_dir = 0

        self._volume = 0

        self._pump_address = pump_address

        self.dual_syringe = dual_syringe

        self.stop()
        self.set_pump_cal(diameter, max_volume, max_rate, syringe_id)

    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    def __str__(self):
        return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    @property
    def flow_rate(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        Pump _flow_rate variable should always be stored in ml/min.

        For these pumps, the flow_rate variable is considered to be the infuse rate,
        whereas the refill_rate variable is the refill rate.

        :type: float
        """
        rate = self._flow_rate

        if self.units.split('/')[1] == 's':
            rate = rate/60.

        if self.units.split('/')[0] == 'uL':
            rate = rate*1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate*1.e6

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s infuse flow rate to %f %s", self.name, rate, self.units)

        if self.units.split('/')[0] == 'uL':
            rate = rate/1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate/1.e6

        if self.units.split('/')[1] == 's':
            rate = rate*60.

        self._flow_rate = self.round(rate)

        self.send_cmd("RAT{}".format(self._flow_rate))


    @property
    def refill_rate(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        Pump _refill_rate variable should always be stored in ml/min.

        For these pumps, the refill_rate variable is considered to be the infuse rate,
        whereas the refill_rate variable is the refill rate.

        :type: float
        """
        rate = self._refill_rate

        if self.units.split('/')[1] == 's':
            rate = rate/60.

        if self.units.split('/')[0] == 'uL':
            rate = rate*1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate*1.e6

        return rate

    @refill_rate.setter
    def refill_rate(self, rate):
        logger.info("Setting pump %s refill flow rate to %f %s", self.name, rate, self.units)

        if self.units.split('/')[0] == 'uL':
            rate = rate/1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate/1.e6

        if self.units.split('/')[1] == 's':
            rate = rate*60.

        self._refill_rate = self.round(rate)

        self.send_cmd("RAT{}".format(self._refill_rate))


    @property
    def volume(self):
        volume = self._volume

        if self._is_dispensing:
            vol = self.get_delivered_volume()

            if self._flow_dir > 0:
                volume = volume - vol
            elif self._flow_dir < 0:
                volume = volume + vol

        return volume

    @volume.setter
    def volume(self, volume):

        if self._is_dispensing:
            vol = self.get_delivered_volume()

            if self._flow_dir > 0:
                volume = volume + vol
            elif self._flow_dir < 0:
                volume = volume - vol

        self._volume = volume

    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.
        """

        logger.debug("Sending pump %s cmd %r", self.name, cmd)

        self.comm_lock.acquire()

        ret = self.pump_comm.write("{}{}".format(self._pump_address, cmd),
            get_response=get_response, send_term_char='\r', term_char='\x03')

        self.comm_lock.release()

        if get_response:
            ret = ret.lstrip('\x02').rstrip('\x03').lstrip(self._pump_address)

            status = ret[0]
            ret = ret[1:]

            logger.debug("Pump %s returned %r", self.name, ret)
        else:
            ret = None
            status = None

        return ret, status

    def is_moving(self):
        """
        Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        ret, status = self.send_cmd("")

        if status == 'I' or status == 'W' or status == 'X' or status == 'T':
            moving = True
        else:
            moving = False

        return moving

    def get_delivered_volume(self):
        ret, status = self.send_cmd("DIS")

        if self._flow_dir > 0:
            vol = ret.split('W')[0].lstrip('I').rstrip('W')
        else:
            vol = ret.split('W')[1].lstrip('I').rstrip('W')[:-2]
        vol = float(vol)

        return vol

    def dispense_all(self, blocking=True):
        if self._is_flowing or self._is_dispensing:
            logger.debug("Stopping pump %s current motion before infusing", self.name)
            self.stop()

        self.dispense(self.volume, blocking=blocking)

    def dispense(self, vol, units='mL', blocking=True):
        """
        Dispenses a fixed volume.

        :param vol: Volume to dispense
        :type vol: float

        :param units: Volume units, defaults to mL, also accepts uL or nL
        :type units: str
        """
        if units == 'uL':
            vol = vol/1000.
        elif units == 'nL':
            vol = vol/1e6

        if self._is_flowing or self._is_dispensing:
            logger.debug("Stopping pump %s current motion before infusing", self.name)
            self.stop()

        cont = True

        if self.volume - vol < 0:
            logger.error(("Attempting to infuse {} mL, which is more than the "
                "current volume of the syringe ({} mL)".format(vol, self.volume)))
            cont = False

        if vol <= 0:
            logger.error(("Infuse volume must be positive."))
            cont = False

        if cont:
            vol = self.round(vol)

            logger.info("Pump %s infusing %f %s at %f %s", self.name, vol, units, self.flow_rate, self.units)

            self.send_cmd("DIRINF")
            self.send_cmd("CLDINF")
            self.send_cmd("RAT{}MM".format(self._flow_rate))
            self.send_cmd("VOL{}".format(vol))
            self.send_cmd("RUN")

            self._is_dispensing = True
            self._flow_dir = 1

    def aspirate_all(self):
        if self._is_flowing or self._is_dispensing:
            logger.debug("Stopping pump %s current motion before aspirating", self.name)
            self.stop()

        if self.round(self.max_volume - self.volume) > 0:
            self.aspirate(self.max_volume - self.volume)
        else:
            logger.error(("Already at maximum volume, can't aspirate more."))

    def aspirate(self, vol, units='mL'):
        """
        Aspirates a fixed volume.

        :param vol: Volume to aspirate
        :type vol: float

        :param units: Volume units, defaults to mL, also accepts uL or nL
        :type units: str
        """

        if units == 'uL':
            vol = vol/1000.
        elif units == 'nL':
            vol = vol/1e6

        if self._is_flowing or self._is_dispensing:
            logger.debug("Stopping pump %s current motion before refilling", self.name)
            self.stop()

        cont = True

        if self.volume + vol > self.max_volume:
            logger.error(("Attempting to refill {} mL, which will take the total "
                "loaded volume to more than the maximum volume of the syringe "
                "({} mL)".format(vol, self.max_volume)))
            cont = False

        if vol <= 0:
            logger.error(("Refill volume must be positive."))
            cont = False

        if cont:
            vol = self.round(vol)

            logger.info("Pump %s refilling %f %s at %f %s", self.name, vol, units, self.refill_rate, self.units)

            self.send_cmd("DIRWDR")
            self.send_cmd("CLDWDR")
            self.send_cmd("RAT{}MM".format(self._refill_rate))
            self.send_cmd("VOL{}".format(vol))
            self.send_cmd("RUN")

            self._is_dispensing = True
            self._flow_dir = -1

    def stop(self):
        """Stops all pump flow."""
        logger.info("Pump %s stopping all motions", self.name)
        self.send_cmd("STP")

        if self._is_dispensing:
            vol = self.get_delivered_volume()

            if self._flow_dir > 0:
                self._volume = self._volume - vol
            elif self._flow_dir < 0:
                self._volume = self._volume + vol

        self._is_dispensing = False
        self._is_flowing = False
        self._flow_dir = 0

    def set_pump_cal(self, diameter, max_volume, max_rate, syringe_id):
        self.diameter = self.round(diameter)
        self.max_volume = max_volume
        self.max_rate = max_rate
        self.syringe_id = syringe_id

        self.send_cmd("DIA{}".format(self.diameter))
        self.send_cmd("VOLML")

    def round(self, val):
        val = float(val)
        if abs(val) < 10:
            val = round(val, 3)
        elif abs(val) >= 10 and abs(val) < 100:
            val = round(val, 2)
        elif abs(val) >= 100 and abs(val) < 1000:
            val = round(val, 1)
        else:
            round(val, 0)
            val = int(val)

        return val

    def disconnect(self):
        """Close any communication connections"""
        logger.debug("Closing pump %s serial connection", self.name)
        self.pump_comm.ser.close()


class SSINextGenPump(Pump):
    """
    .. todo:: This class doesn't know when the pump is done dispensing. This leads
        to unncessary stop signals being sent to the pump, and makes the log harder
        to follow. This could be fixed, when I have time.

    This class contains information for initializing and communicating with
    a VICI M50 Pump using an MForce Controller. Below is an example that
    initializes an M50 pump, starts a flow of 2000 uL/min, and then stops the flow. ::

        >>> my_pump = M50Pump('COM6', 'pump2', flow_cal=626.2, backlash_cal=9.278)
        >>> my_pump.flow_rate = 2000
        >>> my_pump.start_flow()
        >>> my_pump.stop_flow()
    """

    def __init__(self, device, name, comm_lock=threading.Lock()):
        """
        This makes the initial serial connection, and then sets the MForce
        controller parameters to the correct values.

        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """
        Pump.__init__(self, device, name)

        logstr = ("Initializing pump {} on serial port {}".format(self.name,
            self.device, flow_cal, backlash_cal))
        logger.info(logstr)

        self.comm_lock = comm_lock

        self.comm_lock.acquire()
        self.pump_comm = SerialComm(device)
        self.comm_lock.release()

        self.keypad_enable(False)

        # All internal variables are stored in mL/min and psi, regardless of user/pump units
        self._units = 'mL/min'
        self.presure_unit = 'psi'
        self._pump_pressure_unit = 'psi'
        self._flow_rate = 0 #Current set flow rate
        self._max_perssure = 10000 #Upper pressure limit
        self._min_pressure = 0 #Lower pressure limit
        self._pressure = 0
        self._is_flowing = False
        self._max_flow_rate = 10
        self._min_flow_rate = 0
        self.motor_stall_fault = False
        self.upl_fault = False
        self.lpl_fault = False
        self.leak_fault = False
        self.fault = False

        #Make sure parameters are set right
        ret = self.send_cmd('MF') #Max flow rate for the pump
        if ret.startswith('OK') and ret.endswith('/'):
            self._pump_max_flow_rate = float(ret.split(':')[-1].strip('/'))
        else:
            self._pump_max_flow_rate = -1

        ret = self.send_cmd('PR') #Max presure for the pump
        if ret.startswith('OK') and ret.endswith('/'):
            self._pump_max_pressure = float(ret.split(',')[-1].strip('/'))
        else:
            self._pump_max_pressure = -1

        ret = self.send_cmd('PU') #Pressure unit for the pump
        if ret.startswith('OK') and ret.endswith('/'):
            self._pump_pressure_unit = ret.split(',')[-1].strip('/')

        if self._pump_pressure_unit.lower() == 'mpa':
            if self._pump_max_pressure != -1:
                self._pump_max_pressure *= 145.038

        elif self._pump_pressure_unit == 'bar':
            if self._pump_max_pressure != -1:
                self._pump_max_pressure *= 14.5038

        if self._pump_max_pressure != -1:
            self._max_perssure = self._pump_max_pressure

        ret = self.send_cmd('LP')
        if ret.startswith('OK') and ret.endswith('/'):
            val = loat(ret.split(':')[-1].strip('/'))

            if self._pump_pressure_unit == 'mpa':
                val *= 145.038
            elif self._pump_pressure_unit =='bar':
                val *= 14.5038

            self._min_pressure = val

        ret = self.send_cmd('UP')
        if ret.startswith('OK') and ret.endswith('/'):
            val = loat(ret.split(':')[-1].strip('/'))

            if self._pump_pressure_unit == 'mpa':
                val *= 145.038
            elif self._pump_pressure_unit =='bar':
                val *= 14.5038

            self._max_perssure = val


        ret = self.send_cmd('LM1') #Detected leak does not cause fault



        self.get_status()
        self.get_faults()

    @property
    def flow_rate(self):
        rate = self._flow_rate

        if self.units.split('/')[0] == 'uL':
            rate = rate*1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate*1.e6

        if self.units.split('/')[1] == 's':
            rate = rate/60.

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s flow rate to %f %s", self.name, rate, self.units)

        if self.units.split('/')[0] == 'uL':
            rate = rate/1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate/1.e6

        if self.units.split('/')[1] == 's':
            rate = rate*60.

        #Maximum continuous flow rate is 25 mL/min
        if rate>self.max_flow_rate:
            rate = self.max_flow_rate
            logger.warning("Requested flow rate > %f %s, setting pump %s flow rate to %f %s",
                self.max_flow_rate, self.units, self.name, self.max_flow_rate, self.units)

        self._flow_rate = rate

        self.send_cmd('FI{}'.format(self.round_vals(rate, 5)))

    @property
    def max_flow_rate(self):
        rate = self._max_flow_rate

        if self.units.split('/')[0] == 'uL':
            rate = rate*1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate*1.e6

        if self.units.split('/')[1] == 's':
            rate = rate/60.

        return rate

    @max_flow_rate.setter
    def max_flow_rate(self, rate):
        logger.info("Setting pump %s max flow rate to %f %s", self.name, rate, self.units)

        if self.units.split('/')[0] == 'uL':
            rate = rate/1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate/1.e6

        if self.units.split('/')[1] == 's':
            rate = rate*60.

        if self._pump_max_flow_rate != -1 and rate > self._pump_max_flow_rate:
            rate = self._pump_max_flow_rate
            logger.warning('Requested max flow rate %f is greater than the pump maximum '
                'flow rate %f. Setting the maximum flow rate the pump maximum', rate,
                self._pump_max_flow_rate)

        self._max_flow_rate = rate

        if self._flow_rate > rate:
            self.flow_rate = rate

    @property
    def max_pressure(self):
        pressure = self._max_perssure

        if self.pressure_unit.lower == 'mpa':
            pressure = pressure/145.038
        elif self.pressure_unit == 'bar':
            pressure = pressure/14.5038

        return pressure

    @max_pressure.setter
    def max_pressure(self, input_pressure):
        logger.info("Setting pump %s max pressure to %f %s", self.name, input_pressure, self.pressure_units)

        if self.pressure_unit.lower == 'mpa':
            pressure = input_pressure*145.038
        elif self.pressure_unit == 'bar':
            pressure = input_pressure*14.5038

        if self._pump_max_pressure != -1 and pressure > self._pump_max_pressure:
            pressure = self._pump_max_pressure
            logger.warning('Requested max flow rate %f is greater than the pump maximum '
                'flow rate %f. Setting the maximum flow rate the pump maximum', rate,
                self._pump_max_pressure)

        self._max_perssure = pressure

        if self._pump_pressure_unit.lower == 'mpa':
            pressure = pressure*100/145.038 #There's weirdness in how you send the pressure command
        elif self._pump_pressure_unit == 'bar':
            pressure = pressure*10/14.5038

        self.send_cmd('UP{}'.format(self.round_vals(pressure, 5)))

    @property
    def min_pressure(self):
        pressure = self._min_pressure

        if self.pressure_unit.lower == 'mpa':
            pressure = pressure/145.038
        elif self.pressure_unit == 'bar':
            pressure = pressure/14.5038

        return pressure

    @min_pressure.setter
    def min_pressure(self, input_pressure):
        logger.info("Setting pump %s min pressure to %f %s", self.name, input_pressure, self.pressure_units)

        if self.pressure_unit.lower == 'mpa':
            pressure = input_pressure*145.038
        elif self.pressure_unit == 'bar':
            pressure = input_pressure*14.5038

        self._min_pressure = min(0, pressure)

        if self._pump_pressure_unit.lower == 'mpa':
            pressure = pressure*100/145.038 #There's weirdness in how you send the pressure command
        elif self._pump_pressure_unit == 'bar':
            pressure = pressure*10/14.5038

        self.send_cmd('LP{}'.format(self.round_vals(pressure, 5)))

    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.
        :type cmd: str, bytes

        :param get_response: Whether the program should get a response from the pump
        :type get_response: bool
        """
        logger.debug("Sending pump %s cmd %r", self.name, cmd)

        self.comm_lock.acquire()
        ret = self.pump_comm.write(cmd, get_response, '\r', '/')
        self.comm_lock.release()

        if get_response:
            logger.debug("Pump %s returned %r", self.name, ret)

        return ret


    def is_moving(self):
        status = self.send_cmd("PR MV")

        status = status.split('\r\n')[-2][-1]
        status = bool(int(status))

        logger.debug("Pump %s moving: %s", self.name, str(status))

        return status

    def start_flow(self):
        logger.info("Pump %s starting continuous flow at %f %s", self.name, self.flow_rate, self.units)
        self.send_cmd("RU")

        while not self._is_flowing:
            self.get_status()

    def stop(self):
        logger.info("Pump %s stopping all motions", self.name)
        self.send_cmd("ST")

        while self._is_flowing:
            self.get_status()

    def disconnect(self):
        logger.debug("Closing pump %s serial connection", self.name)
        self.pump_comm.ser.close()

    def round_vals(self, val, places):
        if val < 10:
            dec = max(0, places-2)
            rounded = round(val, dec)
        elif val > 10 and val < 100:
            dec = max(0, places-3)
            rounded = round(val, dec)
        elif val >= 100 and val < 1000:
            dec = max(0, places-4)
            rounded = round(val, dec)
        elif val >= 1000 and < 10000:
            dec = max(0, places-5)
            rounded = round(val, dec)
        elif val >= 10000:
            dec = max(0, places-6)
            rounded = round(val, dec)

        return '{:0{size}}'.format(rounded, size=places)

    def get_status(self):
        ret = self.send_cmd('CS')

        if ret.startswith('OK') and ret.endswith('/'):
            vals = ret.split(',')
            self._flow_rate = float(vals[1])
            self._max_perssure = float(vals[2])

            if self._pump_pressure_unit == 'mpa':
                self._max_perssure *= 145.038
            elif self._pump_pressure_unit =='bar':
                self._max_perssure *= 14.5038

            self._min_pressure = float(vals[3])

            if self._pump_pressure_unit == 'mpa':
                self._min_perssure *= 145.038
            elif self._pump_pressure_unit =='bar':
                self._min_perssure *= 14.5038

            self._pressure_unit = vals[4]

            if vals[6] == '0':
                self._is_flowing = False
            else:
                self._is_flowing = True

    def get_faults(self):
        ret = self.send_cmd('RF')

        if ret.startswith('OK') and ret.endswith('/'):
            vals = ret.split(',')

            if vals[1] == '0':
                self.motor_stall_fault = False
            else:
                self.motor_stall_fault = True

            if vals[2] == '0':
                self.upl_fault = False
            else:
                self.upl_fault = True

            if vals[3] == '0':
                self.lpl_fault = False
            else:
                self.lpl_fault = True

        ret = self.send_cmd('LS')

        if ret.startswith('OK') and ret.endswith('/'):
            val = int(ret.split(':').strip('/'))

            if val == '0':
                self.leak_fault = False
            else:
                self.leak_fault = True

        self.fault = self.motor_stall_fault or self.upl_fault or self.lpl_fault or self.leak_fault

    def get_pressure(self):
        ret = self.send_cmd('PR')

        if ret.startswith('OK') and ret.endswith('/'):
            val = float(ret.split(',')[-1].strip('/'))

            if self._pump_pressure_unit == 'mpa':
                val *= 145.038
            elif self._pump_pressure_unit =='bar':
                val *= 14.5038

            if self.pressure_unit.lower == 'mpa':
                pressure = val*145.038
            elif self.pressure_unit == 'bar':
                pressure = val*14.5038

        else:
            pressure = -1

        return pressure

    def clear_faults(self):
        self.send_cmd('#')
        self.send_cmd('CF')
        self.get_faults()

    def keypad_enable(self, enable):
        if enable:
            self.send_cmd('KE')
        else:
            self.send_cmd('KD')



class SoftPump(Pump):
    """
    This class contains the settings and communication for a generic pump.
    It is intended to be subclassed by other pump classes, which contain
    specific information for communicating with a given pump. A pump object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, device, name):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """
        Pump.__init__(self, device, name)

        self._is_flowing = False
        self._is_dispensing = False
        self._is_aspirating = False

        self._units = 'mL/min'
        self._flow_rate = 0
        self._refill_rate = 0
        self._flow_dir = 0

        self._dispensing_volume = 0
        self._aspirating_volume = 0

        self._pump_address = 'Simulated'

        self._connected = True

        self.sim_thread = threading.Thread(target=self._sim_flow)
        self.sim_thread.daemon = True
        self.sim_thread.start()

    @property
    def flow_rate(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        :type: float
        """
        return self._flow_rate

    @flow_rate.setter
    def flow_rate(self, rate):
        self._flow_rate = rate

    @property
    def units(self):
        """
        Sets and returns the pump flow rate units. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :type: str
        """
        return self._units

    @units.setter
    def units(self, units):
        old_units = self._units
        flow_rate = self.flow_rate

        if units in ['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min']:
            self._units = units
            old_vu, old_tu = old_units.split('/')
            new_vu, new_tu = self._units.split('/')[0]
            if old_vu != new_vu:
                if (old_vu == 'nL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'mL'):
                    flow_rate = flow_rate/1000.
                elif old_vu == 'nL' and new_vu == 'mL':
                    flow_rate = flow_rate/1000000.
                elif (old_vu == 'mL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'nL'):
                    flow_rate = flow_rate*1000.
                elif old_vu == 'mL' and new_vu == 'nL':
                    flow_rate = flow_rate*1000000.
            if old_tu != new_tu:
                if old_tu == 'min':
                    flow_rate = flow_rate/60
                else:
                    flow_rate = flow_rate*60

            self._flow_rate = flow_rate

            logger.info("Changed pump %s units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change pump %s units, units supplied were invalid: %s", self.name, units)

    def is_moving(self):
        """
        Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        return self._is_flowing

    def start_flow(self):
        """
        Starts a continuous flow at the flow rate specified by the
        ``Pump.flow_rate`` variable.
        """
        self._is_flowing = True

    def dispense(self, vol, units='uL'):
        """
        Dispenses a fixed volume.

        :param vol: Volume to dispense
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        if units == 'uL':
            vol = vol/1000
        elif units == 'nL':
            vol = vol/1e6

        self._dispensing_volume = vol
        self._is_dispensing = True
        self._is_flowing = True

        pass #Should be implimented in each subclass

    def aspirate(self, vol, units='uL'):
        """
        Aspirates a fixed volume.

        :param vol: Volume to aspirate
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        if units == 'uL':
            vol = vol/1000
        elif units == 'nL':
            vol = vol/1e6

        self._aspirating_volume = vol
        self._is_aspirating = True
        self._is_flowing = True

        pass #Should be implimented in each subclass

    def _get_flow_rate_ml_s(self):
        units = self._units
        flow_rate = self.flow_rate

        if units in ['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min']:
            old_vu, old_tu = units.split('/')
            new_vu, new_tu = 'mL/s'.split('/')
            if old_vu != new_vu:
                if (old_vu == 'nL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'mL'):
                    flow_rate = flow_rate/1000.
                elif old_vu == 'nL' and new_vu == 'mL':
                    flow_rate = flow_rate/1000000.
                elif (old_vu == 'mL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'nL'):
                    flow_rate = flow_rate*1000.
                elif old_vu == 'mL' and new_vu == 'nL':
                    flow_rate = flow_rate*1000000.
            if old_tu != new_tu:
                if old_tu == 'min':
                    flow_rate = flow_rate/60
                else:
                    flow_rate = flow_rate*60

        return flow_rate

    def _sim_flow(self):
        previous_time = time.time()

        while self._connected:
            flow_rate = self._get_flow_rate_ml_s()

            if self._is_dispensing:
                delta_vol = flow_rate*(time.time()-previous_time)
                previous_time = time.time()
                self._dispensing_volume = self._dispensing_volume - delta_vol

                if self._dispensing_volume <= 0:
                    self.stop()

            elif self._is_aspirating:
                delta_vol = flow_rate*(time.time()-previous_time)
                previous_time = time.time()
                self._aspirating_volume = self._aspirating_volume - delta_vol

                if self._aspirating_volume <= 0:
                    self.stop()
            else:
                previous_time = time.time()

            time.sleep(0.1)

    def stop(self):
        """Stops all pump flow."""
        self._is_flowing = False
        self._is_dispensing = False
        self._is_aspirating = False

    def disconnect(self):
        """Close any communication connections"""
        self._connected = False
        self.sim_thread.join()

class SoftSyringePump(Pump):
    """
    This class contains the settings and communication for a generic pump.
    It is intended to be subclassed by other pump classes, which contain
    specific information for communicating with a given pump. A pump object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, device, name, diameter, max_volume, max_rate, syringe_id,
        dual_syringe=False):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """

        Pump.__init__(self, device, name)

        self._is_flowing = False
        self._is_dispensing = False
        self._is_aspirating = False


        self._units = 'mL/min'
        self._flow_rate = 0
        self._refill_rate = 0
        self._flow_dir = 0

        self._dispensing_volume = 0
        self._aspirating_volume = 0
        self._volume = 0

        self._pump_address = 'Simulated'
        self.dual_syringe = dual_syringe

        self._connected = True

        self.set_pump_cal(diameter, max_volume, max_rate, syringe_id)

        self.sim_thread = threading.Thread(target=self._sim_flow)
        self.sim_thread.daemon = True
        self.sim_thread.start()

    @property
    def flow_rate(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        :type: float
        """
        return self._flow_rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s infuse flow rate to %f %s", self.name, rate, self.units)
        self._flow_rate = rate

    @property
    def refill_rate(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        Pump _refill_rate variable should always be stored in ml/min.

        For these pumps, the refill_rate variable is considered to be the infuse rate,
        whereas the refill_rate variable is the refill rate.

        :type: float
        """
        rate = self._refill_rate

        if self.units.split('/')[1] == 's':
            rate = rate/60.

        if self.units.split('/')[0] == 'uL':
            rate = rate*1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate*1.e6

        return rate

    @refill_rate.setter
    def refill_rate(self, rate):
        logger.info("Setting pump %s refill flow rate to %f %s", self.name, rate, self.units)

        if self.units.split('/')[0] == 'uL':
            rate = rate/1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate/1.e6

        if self.units.split('/')[1] == 's':
            rate = rate*60.

        self._refill_rate = rate

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, volume):
        self._volume = volume

    @property
    def units(self):
        """
        Sets and returns the pump flow rate units. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :type: str
        """
        return self._units

    @units.setter
    def units(self, units):
        old_units = self._units
        flow_rate = self.flow_rate

        if units in ['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min']:
            self._units = units
            old_vu, old_tu = old_units.split('/')
            new_vu, new_tu = self._units.split('/')[0]
            if old_vu != new_vu:
                if (old_vu == 'nL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'mL'):
                    flow_rate = flow_rate/1000.
                elif old_vu == 'nL' and new_vu == 'mL':
                    flow_rate = flow_rate/1000000.
                elif (old_vu == 'mL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'nL'):
                    flow_rate = flow_rate*1000.
                elif old_vu == 'mL' and new_vu == 'nL':
                    flow_rate = flow_rate*1000000.
            if old_tu != new_tu:
                if old_tu == 'min':
                    flow_rate = flow_rate/60
                else:
                    flow_rate = flow_rate*60

            self._flow_rate = flow_rate

            logger.info("Changed pump %s units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change pump %s units, units supplied were invalid: %s", self.name, units)

    def is_moving(self):
        """
        Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        return self._is_flowing

    def start_flow(self):
        """
        Starts a continuous flow at the flow rate specified by the
        ``Pump.flow_rate`` variable.
        """
        self._is_flowing = True

    def dispense_all(self):
        if self._is_flowing or self._is_dispensing or self._is_aspirating:
            logger.debug("Stopping pump %s current motion before infusing", self.name)
            self.stop()

        self.dispense(self.volume, self.units.split('/')[0])

    def dispense(self, vol, units='uL'):
        """
        Dispenses a fixed volume.

        :param vol: Volume to dispense
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        if units == 'uL':
            vol = vol/1000
        elif units == 'nL':
            vol = vol/1e6

        if self._is_flowing or self._is_dispensing or self._is_aspirating:
            logger.debug("Stopping pump %s current motion before infusing", self.name)
            self.stop()

        cont = True

        if self.volume - vol < 0:
            logger.error(("Attempting to infuse {} mL, which is more than the "
                "current volume of the syringe ({} mL)".format(vol, self.volume)))
            cont = False

        if vol <= 0:
            logger.error(("Infuse volume must be positive."))
            cont = False

        if cont:
            self._dispensing_volume = vol
            self._is_dispensing = True
            self._is_flowing = True

    def aspirate_all(self):
        if self._is_flowing or self._is_dispensing or self._is_aspirating:
            logger.debug("Stopping pump %s current motion before aspirating", self.name)
            self.stop()

        if self.max_volume - self.volume > 0:
            self.aspirate(self.max_volume - self.volume, self.units.split('/')[0])
        else:
            logger.error(("Already at maximum volume, can't aspirate more."))

    def aspirate(self, vol, units='uL'):
        """
        Aspirates a fixed volume.

        :param vol: Volume to aspirate
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        if units == 'uL':
            vol = vol/1000
        elif units == 'nL':
            vol = vol/1e6

        if self._is_flowing or self._is_dispensing or self._is_aspirating:
            logger.debug("Stopping pump %s current motion before infusing", self.name)
            self.stop()

        cont = True

        if self.volume + vol > self.max_volume:
            logger.error(("Attempting to refill {} mL, which will take the total "
                "loaded volume to more than the maximum volume of the syringe "
                "({} mL)".format(vol, self.max_volume)))
            cont = False

        if vol <= 0:
            logger.error(("Refill volume must be positive."))
            cont = False

        if cont:
            self._aspirating_volume = vol
            self._is_aspirating = True
            self._is_flowing = True

    def _get_flow_rate_ml_s(self):
        units = self._units
        flow_rate = self.flow_rate

        if units in ['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min']:
            old_vu, old_tu = units.split('/')
            new_vu, new_tu = 'mL/s'.split('/')
            if old_vu != new_vu:
                if (old_vu == 'nL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'mL'):
                    flow_rate = flow_rate/1000.
                elif old_vu == 'nL' and new_vu == 'mL':
                    flow_rate = flow_rate/1000000.
                elif (old_vu == 'mL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'nL'):
                    flow_rate = flow_rate*1000.
                elif old_vu == 'mL' and new_vu == 'nL':
                    flow_rate = flow_rate*1000000.
            if old_tu != new_tu:
                if old_tu == 'min':
                    flow_rate = flow_rate/60
                else:
                    flow_rate = flow_rate*60

        return flow_rate

    def _get_refill_rate_ml_s(self):
        units = self._units
        flow_rate = self.refill_rate

        if units in ['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min']:
            old_vu, old_tu = units.split('/')
            new_vu, new_tu = 'mL/s'.split('/')
            if old_vu != new_vu:
                if (old_vu == 'nL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'mL'):
                    flow_rate = flow_rate/1000.
                elif old_vu == 'nL' and new_vu == 'mL':
                    flow_rate = flow_rate/1000000.
                elif (old_vu == 'mL' and new_vu == 'uL') or (old_vu == 'uL' and new_vu == 'nL'):
                    flow_rate = flow_rate*1000.
                elif old_vu == 'mL' and new_vu == 'nL':
                    flow_rate = flow_rate*1000000.
            if old_tu != new_tu:
                if old_tu == 'min':
                    flow_rate = flow_rate/60
                else:
                    flow_rate = flow_rate*60

        return flow_rate

    def _convert_volume_ml(self, volume):
        vol_units = self.units.split('/')[0]

        if vol_units == 'uL':
            volume = volume*1000
        elif vol_units == 'nL':
            volume = volume*1e6

        return volume

    def _sim_flow(self):
        previous_time = time.time()

        while self._connected:
            if self._is_dispensing:
                flow_rate = self._get_flow_rate_ml_s()

                delta_vol = flow_rate*(time.time()-previous_time)
                previous_time = time.time()
                self._dispensing_volume = self._dispensing_volume - delta_vol

                if self._dispensing_volume <= 0:
                    self.stop()

                delta_vol_cor = self._convert_volume_ml(delta_vol)
                self.volume = self.volume - delta_vol_cor

            elif self._is_aspirating:
                flow_rate = self._get_refill_rate_ml_s()

                delta_vol = flow_rate*(time.time()-previous_time)
                previous_time = time.time()
                self._aspirating_volume = self._aspirating_volume - delta_vol

                if self._aspirating_volume <= 0:
                    self.stop()

                delta_vol_cor = self._convert_volume_ml(delta_vol)
                self.volume = self.volume + delta_vol_cor

            else:
                previous_time = time.time()

            time.sleep(0.1)

    def set_pump_cal(self, diameter, max_volume, max_rate, syringe_id):
        self.diameter = diameter
        self.max_volume = max_volume
        self.max_rate = max_rate
        self.syringe_id = syringe_id

    def stop(self):
        """Stops all pump flow."""
        self._is_flowing = False
        self._is_dispensing = False
        self._is_aspirating = False

    def disconnect(self):
        """Close any communication connections"""
        self._connected = False
        self.sim_thread.join()


class PumpCommThread(threading.Thread):
    """
    This class creates a control thread for pumps attached to the system.
    This thread is designed for using a GUI application. For command line
    use, most people will find working directly with a pump object much
    more transparent. Below you'll find an example that initializes an
    :py:class:`M50Pump`, starts a flow of 2000 uL/min, and stops the flow
    5 s later. ::

        import collections
        import threading

        pump_cmd_q = collections.deque()
        abort_event = threading.Event()
        my_pumpcon = PumpCommThread(pump_cmd_q, abort_event)
        my_pumpcon.start()

        init_cmd = ('connect', ('COM6', 'pump2', 'VICI_M50'),
            {'flow_cal': 626.2, 'backlash_cal': 9.278})
        flow_rate_cmd = ('set_flow_rate', ('pump2', 2000), {})
        start_cmd = ('start_flow', ('pump2',), {})
        stop_cmd = ('stop', ('pump2',), {})

        pump_cmd_q.append(init_cmd)
        pump_cmd_q.append(start_cmd)
        pump_cmd_q.append(flow_rate_cmd)
        time.sleep(5)
        pump_cmd_q.append(stop_cmd)

        my_pumpcon.stop()
    """

    def __init__(self, command_queue, return_queue, abort_event, name=None):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_pumps``.

        :param collections.deque command_queue: The queue used to pass commands to
            the thread.

        :param threading.Event abort_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        threading.Thread.__init__(self, name=name)
        self.daemon = True

        logger.info("Starting pump control thread: %s", self.name)

        self.command_queue = command_queue
        self.return_queue = return_queue
        self._abort_event = abort_event
        self._stop_event = threading.Event()

        self._commands = {'connect'     : self._connect_pump,
                        'set_flow_rate' : self._set_flow_rate,
                        'set_refill_rate': self._set_refill_rate,
                        'set_units'     : self._set_units,
                        'start_flow'    : self._start_flow,
                        'stop'          : self._stop_flow,
                        'aspirate'      : self._aspirate,
                        'dispense'      : self._dispense,
                        'is_moving'     : self._is_moving,
                        'send_cmd'      : self._send_cmd,
                        'disconnect'    : self._disconnect_pump,
                        'get_volume'    : self._get_volume,
                        'set_volume'    : self._set_volume,
                        'dispense_all'  : self._dispense_all,
                        'aspirate_all'  : self._aspirate_all,
                        'set_pump_cal'  : self._set_pump_cal,
                        'add_pump'      : self._add_pump,
                        'add_comlocks'  : self._add_comlocks,
                        'connect_remote': self._connect_pump_remote,
                        'get_status'    : self._get_status,
                        'get_status_multi': self._get_status_multiple,
                        'set_pump_dual_syringe': self._set_dual_syringe,
                        }

        self._connected_pumps = OrderedDict()

        self.comm_locks = {}

        self.known_pumps = {'VICI_M50'      : M50Pump,
                            'PHD_4400'      : PHD4400Pump,
                            'NE_500'        : NE500Pump,
                            'Soft'          : SoftPump,
                            'Soft_Syringe'  : SoftSyringePump,
                            }

    def run(self):
        """
        Custom run method for the thread.
        """
        while True:
            if len(self.command_queue) > 0:
                logger.debug("Getting new command")
                command, args, kwargs = self.command_queue.popleft()
            else:
                command = None

            if self._abort_event.is_set():
                logger.debug("Abort event detected")
                self._abort()
                command = None

            if self._stop_event.is_set():
                logger.debug("Stop event detected")
                self._abort()
                break

            if command is not None:
                logger.debug("Processing cmd '%s' with args: %s and kwargs: %s ", command, ', '.join(['{}'.format(a) for a in args]), ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()]))
                try:
                    self._commands[command](*args, **kwargs)
                except Exception:
                    msg = ("Pump control thread failed to run command '%s' "
                        "with args: %s and kwargs: %s " %(command,
                        ', '.join(['{}'.format(a) for a in args]),
                        ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()])))
                    logger.exception(msg)

                    if command == 'connect':
                        self.return_queue.append((args[1], 'connect', False))
                    elif command == 'disconnect':
                        self.return_queue.append((args[0], 'disconnect', False))

            else:
                time.sleep(0.01)

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()
        logger.info("Quitting pump control thread: %s", self.name)

    def _connect_pump(self, device, name, pump_type, **kwargs):
        """
        This method connects to a pump by creating a new :py:class:`Pump` subclass
        object (e.g. a new :py:class:`M50Pump` object). This pump is saved in the thread
        and can be called later to do stuff. All pumps must be connected before
        they can be used.

        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str

        :param pump_type: A pump type in the ``known_pumps`` dictionary.
        :type pump_type: str

        :param \*\*kwargs: This function accepts arbitrary keyword args that are passed
            directly to the :py:class:`Pump` subclass that is called. For example,
            for an :py:class:`M50Pump` you could pass ``flow_cal`` and ``backlash``.
        """
        logger.info("Connecting pump %s", name)
        new_pump = self.known_pumps[pump_type](device, name, **kwargs)
        self._connected_pumps[name] = new_pump
        self.return_queue.append((name, 'connect', True))
        logger.debug("Pump %s connected", name)

    def _connect_pump_remote(self, device, name, pump_type, **kwargs):
        """
        This method connects to a pump by creating a new :py:class:`Pump` subclass
        object (e.g. a new :py:class:`M50Pump` object). This pump is saved in the thread
        and can be called later to do stuff. All pumps must be connected before
        they can be used.

        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str

        :param pump_type: A pump type in the ``known_pumps`` dictionary.
        :type pump_type: str

        :param \*\*kwargs: This function accepts arbitrary keyword args that are passed
            directly to the :py:class:`Pump` subclass that is called. For example,
            for an :py:class:`M50Pump` you could pass ``flow_cal`` and ``backlash``.
        """
        logger.info("Connecting pump %s", name)
        if device in self.comm_locks:
            kwargs['comm_lock'] = self.comm_locks[device]
        else:
            logger.info('creating new comlock!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!')

        new_pump = self.known_pumps[pump_type](device, name, **kwargs)

        self._connected_pumps[name] = new_pump
        self.return_queue.append((name, 'connect', True))
        logger.debug("Pump %s connected", name)

    def _disconnect_pump(self, name):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Disconnecting pump %s", name)
        pump = self._connected_pumps[name]
        pump.disconnect()
        del self._connected_pumps[name]
        self.return_queue.append((name, 'disconnect', True))
        logger.debug("Pump %s disconnected", name)

    def _add_pump(self, pump, name, **kwargs):
        logger.info('Adding pump %s', name)
        self._connected_pumps[name] = pump
        self.return_queue.append((name, 'add', True))
        logger.debug('Pump %s added', name)

    def _set_flow_rate(self, name, flow_rate):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        logger.info("Setting pump %s flow rate", name)
        pump = self._connected_pumps[name]
        pump.flow_rate = flow_rate
        logger.debug("Pump %s flow rate set", name)

    def _set_refill_rate(self, name, refill_rate):
        """
        This method sets the refill rate for a pump. Only works for pumps that
        have a refill rate, for example the Harvard syringe pumps.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float refill_rate: The refill rate for the pump.
        """
        logger.info("Setting pump %s refill rate", name)
        pump = self._connected_pumps[name]
        pump.refill_rate = refill_rate
        logger.debug("Pump %s refill rate set", name)

    def _set_units(self, name, units):
        """
        This method sets the units for the flow rate for a pump. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param str units: The units for the pump.
        """
        logger.info("Setting pump %s units", name)
        pump = self._connected_pumps[name]
        pump.units = units
        logger.debug("Pump %s units set", name)

    def _set_volume(self, name, volume):
        """
        This method sets the volume for a fixed volume pump such as a syringe pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float volume: The volume for the pump.
        """
        logger.info("Setting pump %s volume", name)
        pump = self._connected_pumps[name]
        pump.volume = volume
        logger.debug("Pump %s volume set", name)

    def _get_volume(self, name):
        """
        This method gets the volume of a fixed volume pump such as a syringe pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.debug("Getting pump %s volume", name)
        pump = self._connected_pumps[name]
        volume = pump.volume
        self.return_queue.append((name, 'volume', volume))
        logger.debug("Pump %s volume is %f", name, volume)

    def _start_flow(self, name, callback=None):
        """
        This method starts continuous flow for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.info("Starting pump %s continuous flow", name)
        pump = self._connected_pumps[name]
        pump.start_flow()
        self.return_queue.append((name, 'start', True))

        if callback is not None:
            callback()

        logger.debug("Pump %s flow started", name)

    def _stop_flow(self, name):
        """
        This method stops all flow (continuous or finite) for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.info("Stopping pump %s", name)
        pump = self._connected_pumps[name]
        pump.stop()
        self.return_queue.append((name, 'stop', True))
        logger.debug("Pump %s stopped", name)

    def _aspirate(self, name, vol, callback=None, units='uL'):
        """
        This method aspirates a fixed volume.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float vol: The volume to aspirate.

        :param str units: The units of the volume, can be nL, uL, or mL. Defaults to uL.
        """
        logger.info("Aspirating pump %s", name)
        pump = self._connected_pumps[name]
        pump.aspirate(vol, units)
        self.return_queue.append((name, 'start', True))

        if callback is not None:
            callback()

        logger.debug("Pump %s aspiration started", name)

    def _aspirate_all(self, name, callback=None):
        """
        This method aspirates all remaning volume for a fixed volume pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.info("Aspirating all for pump %s", name)
        pump = self._connected_pumps[name]
        pump.aspirate_all()
        self.return_queue.append((name, 'start', True))

        if callback is not None:
            callback()

        logger.debug("Pump %s aspiration started", name)

    def _dispense(self, name, vol, callback=None, units='uL'):
        """
        This method dispenses a fixed volume.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float vol: The volume to dispense.

        :param str units: The units of the volume, can be nL, uL, or mL. Defaults to uL.
        """
        logger.info("Dispensing pump %s", name)
        pump = self._connected_pumps[name]
        pump.dispense(vol, units)
        self.return_queue.append((name, 'start', True))

        if callback is not None:
            callback()

        logger.debug("Pump %s dispensing started", name)

    def _dispense_all(self, name, callback=None):
        """
        This method dispenses all remaining volume for a fixed volume pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.info("Dispensing all from pump %s", name)
        pump = self._connected_pumps[name]
        pump.dispense_all()
        self.return_queue.append((name, 'start', True))

        if callback is not None:
            callback()

        logger.debug("Pump %s dispensing started", name)

    def _is_moving(self, name):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        logger.debug("Checking if pump %s is moving", name)
        pump = self._connected_pumps[name]
        is_moving = pump.is_moving()
        self.return_queue.append((name, 'moving', is_moving))
        logger.debug("Pump %s is moving: %s", name, str(is_moving))

    def _set_pump_cal(self, name, diameter, max_volume, max_rate, syringe_id):
        logger.info("Setting pump %s calibration parameters", name)
        pump = self._connected_pumps[name]
        pump.set_pump_cal(diameter, max_volume, max_rate, syringe_id)
        logger.debug("Pump %s calibration parameters set", name)

    def _set_dual_syringe(self, name, dual_syringe):
        logger.info("Setting pump %s dual syringe to %s", name, str(dual_syringe))
        pump = self._connected_pumps[name]
        pump.dual_syringe = dual_syringe
        logger.debug("Pump %s dual syringe parameter set", name)

    def _get_status(self, name):
        logger.debug("Getting pump status")
        pump = self._connected_pumps[name]
        is_moving = pump.is_moving()
        volume = pump.volume
        self.return_queue.append((name, 'status', (is_moving, volume)))

    def _get_status_multiple(self, names):
        status = []
        for name in names:
            pump = self._connected_pumps[name]
            is_moving = pump.is_moving()
            volume = pump.volume
            status.append((is_moving, volume))

        self.return_queue.append((names, 'multi_status', status))

    def _send_cmd(self, name, cmd, get_response=True):
        """
        This method can be used to send an arbitrary command to the pump.
        If something is going to be used frequently, it probably should be
        added as a pump method.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param cmd: The command to send, in an appropriate format for the pump.

        :param bool get_response: Whether the software should wait for a
            response from the pump. Defaults to ``True``.
        """
        logger.info("Sending pump %s cmd %r", name. cmd)
        pump = self._connected_pumps[name]
        pump.send_cmd(cmd, get_response)
        logger.debug("Pump %s command sent", name)

    def _add_comlocks(self, comm_locks):
        self.comm_locks.update(comm_locks)

    def _abort(self):
        """Clears the ``command_queue`` and aborts all current pump motions."""
        logger.info("Aborting pump control thread %s current and future commands", self.name)
        self.command_queue.clear()

        for name, pump in self._connected_pumps.items():
            pump.stop()

        self._abort_event.clear()
        logger.debug("Pump control thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down pump control thread: %s", self.name)
        self._stop_event.set()

class PumpPanel(wx.Panel):
    """
    This pump panel supports standard flow controls and settings, including
    connection settings, for a pump. It is meant to be embedded in a larger application
    and can be instanced several times, once for each pump. It communciates
    with the pumps using the :py:class:`PumpCommThread`. Currently it only supports
    the :py:class:`M50Pump`, but it should be easy to extend for other pumps. The
    only things that should have to be changed are the are adding in pump-specific
    settings, modeled after how the ``m50_pump_sizer`` is constructed in the
    :py:func:`_create_layout` function, and then add in type switching in the
    :py:func:`_on_type` function.
    """
    def __init__(self, parent, panel_id, panel_name, all_comports, pump_cmd_q,
        pump_answer_q, known_pumps, pump_name, pump_type=None, comport=None,
        pump_args=[], pump_kwargs={}, comm_lock=threading.Lock(), flow_rate='',
        refill_rate=''):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_pumps``.

        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        :param str panel_name: Name for the panel

        :param list all_comports: A list containing all comports that the pump
            could be connected to.

        :param collections.deque pump_cmd_q: The ``pump_cmd_q`` that was passed to
            the :py:class:`PumpCommThread`.

        :param list known_pumps: The list of known pump types, obtained from
            the :py:class:`PumpCommThread`.

        :param str pump_name: An identifier for the pump, displayed in the pump
            panel.

        :param str pump_type: One of the ``known_pumps``, corresponding to the pump
            connected to this panel. Only required if you are connecting the pump
            when the panel is first set up (rather than manually later).

        :param str comport: The comport the pump is connected to. Only required
            if you are connecting the pump when the panel is first set up (rather
            than manually later).

        :param list pump_args: Pump specific arguments for initialization.
            Only required if you are connecting the pump when the panel is first
            set up (rather than manually later).

        :param dict pump_kwargs: Pump specific keyword arguments for initialization.
            Only required if you are connecting the pump when the panel is first
            set up (rather than manually later).

        :param str pump_mode: Either 'continous' for continous flow pumps or
            'syringe' for syringe pumps.Only required if you are connecting the
            pump when the panel is first set up (rather than manually later).

        :param treading.Lock comm_lock: Used for pump communication, prevents
            multiple access on serial ports for pumps in a daisy chain.

        """

        wx.Panel.__init__(self, parent, panel_id, name=panel_name)
        logger.debug('Initializing PumpPanel for pump %s', pump_name)

        self.name = pump_name
        self.pump_cmd_q = pump_cmd_q
        self.all_comports = all_comports
        self.known_pumps = known_pumps
        self.answer_q = pump_answer_q
        self.connected = False
        self.comm_lock = comm_lock

        self.known_syringes = {'30 mL, EXEL': {'diameter': 23.5, 'max_volume': 30,
            'max_rate': 70},
            '3 mL, Medline P.C.': {'diameter': 9.1, 'max_volume': 3.0,
            'max_rate': 11},
            '6 mL, Medline P.C.': {'diameter': 12.8, 'max_volume': 6,
            'max_rate': 23},
            '10 mL, Medline P.C.': {'diameter': 16.31, 'max_volume': 10,
            'max_rate': 31},
            '20 mL, Medline P.C.': {'diameter': 19.84, 'max_volume': 20,
            'max_rate': 55},
            '0.25 mL, Hamilton Glass': {'diameter': 2.30, 'max_volume': 0.25,
            'max_rate': 11},
            '0.5 mL, Hamilton Glass': {'diameter': 3.26, 'max_volume': 0.5,
            'max_rate': 11},
            '1.0 mL, Hamilton Glass': {'diameter': 4.61, 'max_volume': 1.0,
            'max_rate': 11},
            }

        self.top_sizer = self._create_layout(flow_rate, refill_rate)

        self.monitor_flow_evt = threading.Event()
        self.monitor_flow_evt.clear()

        self.monitor_thread = threading.Thread(target=self._monitor_flow)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()

        self.SetSizer(self.top_sizer)

        self._initpump(pump_type, comport, pump_args, pump_kwargs)


    def _create_layout(self, flow_rate='', refill_rate=''):
        """Creates the layout for the panel."""
        self.status = wx.StaticText(self, label='Not connected')
        self.syringe_volume = wx.StaticText(self, label='0', size=(40,-1),
            style=wx.ST_NO_AUTORESIZE)
        self.syringe_volume_label = wx.StaticText(self, label='Current volume:')
        self.syringe_volume_units = wx.StaticText(self, label='mL')
        self.set_syringe_volume = wx.Button(self, label='Set Current Volume')
        self.set_syringe_volume.Bind(wx.EVT_BUTTON, self._on_set_volume)
        self.syringe_vol_gauge = wx.Gauge(self, size=(40, -1),
            style=wx.GA_HORIZONTAL|wx.GA_SMOOTH)
        self.syringe_vol_gauge_low = wx.StaticText(self, label='0')
        self.syringe_vol_gauge_high = wx.StaticText(self, label='')

        self.vol_gauge = wx.BoxSizer(wx.HORIZONTAL)
        self.vol_gauge.Add(self.syringe_vol_gauge_low,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.vol_gauge.Add(self.syringe_vol_gauge, 1, border=2,
            flag=wx.LEFT|wx.EXPAND)
        self.vol_gauge.Add(self.syringe_vol_gauge_high, border=2,
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)

        status_grid = wx.GridBagSizer(vgap=5, hgap=5)
        status_grid.Add(wx.StaticText(self, label='Pump name:'), (0,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(wx.StaticText(self, label=self.name), (0,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        status_grid.Add(wx.StaticText(self, label='Status: '), (1,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.status, (1,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        status_grid.Add(self.syringe_volume_label, (2,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.syringe_volume, (2,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.syringe_volume_units, (2,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.vol_gauge, (3,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        status_grid.Add(self.set_syringe_volume, (4,1), span=(1,2),
            flag=wx.LEFT|wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL)

        self.status_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Info'),
            wx.VERTICAL)
        self.status_sizer.Add(status_grid, 1, wx.EXPAND)

        self.mode_ctrl = wx.Choice(self, choices=['Continuous flow', 'Fixed volume'])
        self.mode_ctrl.SetSelection(0)
        self.direction_ctrl = wx.Choice(self, choices=['Dispense', 'Aspirate'])
        self.direction_ctrl.SetSelection(0)
        self.flow_rate_ctrl = wx.TextCtrl(self, value=flow_rate, size=(60,-1))
        self.flow_units_lbl = wx.StaticText(self, label='mL/min')
        self.refill_rate_lbl = wx.StaticText(self, label='Refill rate:')
        self.refill_rate_ctrl = wx.TextCtrl(self, value=refill_rate, size=(60,-1))
        self.refill_rate_units = wx.StaticText(self, label='mL')
        self.volume_lbl = wx.StaticText(self, label='Volume:')
        self.volume_ctrl = wx.TextCtrl(self, size=(60,-1))
        self.vol_units_lbl = wx.StaticText(self, label='mL')

        self.mode_ctrl.Bind(wx.EVT_CHOICE, self._on_mode)

        basic_ctrl_sizer = wx.GridBagSizer(vgap=2, hgap=2)
        basic_ctrl_sizer.Add(wx.StaticText(self, label='Mode:'), (0,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.mode_ctrl, (0,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(wx.StaticText(self, label='Direction:'), (1,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.direction_ctrl, (1,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(wx.StaticText(self, label='Flow rate:'), (2,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_rate_ctrl, (2,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_units_lbl, (2,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(self.refill_rate_lbl, (3,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.refill_rate_ctrl, (3,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.refill_rate_units, (3,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(self.volume_lbl, (4,0),
            flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.volume_ctrl, (4,1),
            flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.vol_units_lbl, (4,2),
            flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.AddGrowableCol(1)
        basic_ctrl_sizer.SetEmptyCellSize((0,0))


        self.run_button = wx.Button(self, label='Start')
        self.fr_button = wx.Button(self, label='Change flow rate')

        self.run_button.Bind(wx.EVT_BUTTON, self._on_run)
        self.fr_button.Bind(wx.EVT_BUTTON, self._on_fr_change)

        button_ctrl_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_ctrl_sizer.Add(self.run_button, 0, wx.ALIGN_CENTER_VERTICAL)
        button_ctrl_sizer.Add(self.fr_button, 0, wx.ALIGN_CENTER_VERTICAL|wx.RESERVE_SPACE_EVEN_IF_HIDDEN)


        self.type_ctrl = wx.Choice(self,
            choices=[item.replace('_', ' ') for item in self.known_pumps.keys()],
            style=wx.CB_SORT)
        self.type_ctrl.SetSelection(0)
        self.com_ctrl = wx.Choice(self, choices=self.all_comports, style=wx.CB_SORT)
        self.vol_unit_ctrl = wx.Choice(self, choices=['nL', 'uL', 'mL'])
        self.vol_unit_ctrl.SetSelection(2)
        self.time_unit_ctrl = wx.Choice(self, choices=['s', 'min'])
        self.time_unit_ctrl.SetSelection(1)

        self.type_ctrl.Bind(wx.EVT_CHOICE, self._on_type)
        self.vol_unit_ctrl.Bind(wx.EVT_CHOICE, self._on_units)
        self.time_unit_ctrl.Bind(wx.EVT_CHOICE, self._on_units)

        gen_settings_sizer = wx.FlexGridSizer(rows=4, cols=2, vgap=2, hgap=2)
        gen_settings_sizer.AddGrowableCol(1)
        gen_settings_sizer.Add(wx.StaticText(self, label='Pump type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(self.type_ctrl, 1,
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(wx.StaticText(self, label='COM port:'))
        gen_settings_sizer.Add(self.com_ctrl, 1,
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(wx.StaticText(self, label='Volume unit:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(self.vol_unit_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(wx.StaticText(self, label='Time unit:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(self.time_unit_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)


        self.m50_fcal = wx.TextCtrl(self, value='628', size=(60, -1))
        self.m50_bcal = wx.TextCtrl(self, value='1.5', size=(60, -1))

        self.m50_settings_sizer = wx.FlexGridSizer(rows=2, cols=3, vgap=2, hgap=2)
        self.m50_settings_sizer.AddGrowableCol(1)
        self.m50_settings_sizer.Add(wx.StaticText(self, label='Flow Cal.:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.m50_settings_sizer.Add(self.m50_fcal,1,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.m50_settings_sizer.Add(wx.StaticText(self, label='uL/rev.'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.m50_settings_sizer.Add(wx.StaticText(self, label='Backlash:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.m50_settings_sizer.Add(self.m50_bcal, 1,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.m50_settings_sizer.Add(wx.StaticText(self, label='uL'),
            flag=wx.ALIGN_CENTER_VERTICAL)


        syr_types = sorted(self.known_syringes.keys(), key=lambda x: float(x.split()[0]))
        self.syringe_type = wx.Choice(self, choices=syr_types)
        self.syringe_type.SetSelection(0)
        self.syringe_type.Bind(wx.EVT_CHOICE, self._on_syringe_type)
        self.pump_address = wx.TextCtrl(self, size=(60, -1))
        self.dual_syringe = wx.Choice(self, choices=['True', 'False'])
        self.dual_syringe.SetStringSelection('False')

        self.phd4400_settings_sizer = wx.FlexGridSizer(cols=2, vgap=2, hgap=2)
        self.phd4400_settings_sizer.Add(wx.StaticText(self, label='Syringe type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.phd4400_settings_sizer.Add(self.syringe_type,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.phd4400_settings_sizer.Add(wx.StaticText(self, label='Pump address:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.phd4400_settings_sizer.Add(self.pump_address,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.phd4400_settings_sizer.Add(wx.StaticText(self, label='Dual syringes:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.phd4400_settings_sizer.Add(self.dual_syringe,
            flag=wx.ALIGN_CENTER_VERTICAL)


        syr_types = sorted(self.known_syringes.keys(), key=lambda x: float(x.split()[0]))
        self.syringe_type2 = wx.Choice(self, choices=syr_types)
        self.syringe_type2.SetSelection(0)
        self.syringe_type2.Bind(wx.EVT_CHOICE, self._on_syringe_type)

        self.soft_syringe_settings_sizer = wx.FlexGridSizer(cols=2, vgap=2, hgap=2)
        self.soft_syringe_settings_sizer.Add(wx.StaticText(self, label='Syringe type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.soft_syringe_settings_sizer.Add(self.syringe_type2,
            flag=wx.ALIGN_CENTER_VERTICAL)

        self.connect_button = wx.Button(self, label='Connect')
        self.connect_button.Bind(wx.EVT_BUTTON, self._on_connect)


        self.control_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Controls'),
            wx.VERTICAL)
        self.control_box_sizer.Add(basic_ctrl_sizer, flag=wx.EXPAND)
        self.control_box_sizer.Add(button_ctrl_sizer, flag=wx.ALIGN_CENTER_HORIZONTAL|wx.TOP, border=2)

        self.settings_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Settings'),
            wx.VERTICAL)
        self.settings_box_sizer.Add(gen_settings_sizer, flag=wx.EXPAND)
        self.settings_box_sizer.Add(self.m50_settings_sizer, flag=wx.EXPAND|wx.TOP, border=2)
        self.settings_box_sizer.Add(self.phd4400_settings_sizer, flag=wx.EXPAND|wx.TOP, border=2)
        self.settings_box_sizer.Add(self.soft_syringe_settings_sizer, flag=wx.EXPAND|wx.TOP, border=2)
        self.settings_box_sizer.Add(self.connect_button, flag=wx.ALIGN_CENTER_HORIZONTAL|wx.TOP, border=2)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.status_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.control_box_sizer, border=5, flag=wx.EXPAND|wx.TOP)
        top_sizer.Add(self.settings_box_sizer, border=5, flag=wx.EXPAND|wx.TOP)

        self.volume_lbl.Hide()
        self.volume_ctrl.Hide()
        self.vol_units_lbl.Hide()
        self.fr_button.Hide()

        self.settings_box_sizer.Hide(self.m50_settings_sizer, recursive=True)
        self.settings_box_sizer.Hide(self.phd4400_settings_sizer, recursive=True)
        self.settings_box_sizer.Hide(self.soft_syringe_settings_sizer, recursive=True)

        if self.type_ctrl.GetStringSelection() == 'VICI M50':
            self.settings_box_sizer.Show(self.m50_settings_sizer, recursive=True)
            self.pump_mode = 'continuous'

        elif (self.type_ctrl.GetStringSelection() == 'PHD 4400'
            or self.type_ctrl.GetStringSelection() == 'NE 500'):
            self.settings_box_sizer.Show(self.phd4400_settings_sizer, recursive=True)
            self.pump_mode = 'syringe'

        elif self.type_ctrl.GetStringSelection() == 'Soft':
            self.pump_mode = 'continuous'

        elif self.type_ctrl.GetStringSelection() == 'Soft Syringe':
            self.settings_box_sizer.Show(self.soft_syringe_settings_sizer, recursive=True)
            self.pump_mode = 'syringe'

        if self.pump_mode == 'continuous':
            self.status_sizer.Hide(self.vol_gauge, recursive=True)
            self.syringe_volume.Hide()
            self.syringe_volume_units.Hide()
            self.syringe_volume_label.Hide()
            self.set_syringe_volume.Hide()
            self.refill_rate_ctrl.Hide()
            self.refill_rate_lbl.Hide()
            self.refill_rate_units.Hide()

        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()
        self.flow_units_lbl.SetLabel('{}/{}'.format(vol_unit, t_unit))
        self.vol_units_lbl.SetLabel(vol_unit)
        self.syringe_volume_units.SetLabel(vol_unit)
        self.refill_rate_units.SetLabel('{}/{}'.format(vol_unit, t_unit))
        self.Refresh()

        return top_sizer

    def _initpump(self, pump_type, comport, pump_args, pump_kwargs):
        """
        Initializes the pump parameters if any were provided. If enough are
        provided the pump is automatically connected.

        :param str pump_type: The pump type, corresponding to a ``known_pump``.

        :param str comport: The comport the pump is attached to.

        :param list pump_args: The pump positional initialization values.
            Appropriate values depend on the pump.

        :param dict pump_kwargs: The pump key word arguments. Appropriate
            values depend on the pump.
        """
        my_pumps = [item.replace('_', ' ') for item in self.known_pumps.keys()]
        if pump_type in my_pumps:
            # self.type_ctrl.SetStringSelection(pump_type)
            self.type_ctrl.SetSelection(self.type_ctrl.GetStrings().index(pump_type))
            self._on_type(None)

        if comport in self.all_comports:
            self.com_ctrl.SetSelection(self.com_ctrl.GetStrings().index(comport))
            # self.com_ctrl.SetStringSelection(comport)

        if pump_type == 'VICI M50':
            if 'flow_cal' in pump_kwargs.keys():
                self.m50_fcal.ChangeValue(pump_kwargs['flow_cal'])
            if 'backlash' in pump_kwargs.keys():
                self.m50_bcal.ChangeValue(pump_kwargs['backlash'])

            if len(pump_args) >= 1:
                self.m50_fcal.ChangeValue(pump_args[0])
            if len(pump_args) == 2:
                self.m50_bcal.ChangeValue(pump_args[1])

        elif pump_type == 'PHD 4400' or pump_type == 'NE 500':
            if 'syringe' in pump_kwargs.keys():
                self.syringe_type.SetStringSelection(pump_kwargs['syringe'])

            if 'address' in pump_kwargs.keys():
                self.pump_address.SetValue(pump_kwargs['address'])

            if 'dual_syringe' in pump_kwargs.keys():
                self.dual_syringe.SetStringSelection(pump_kwargs['dual_syringe'])

            if len(pump_args) >=1:
                self.syringe_type.SetStringSelection(pump_args[0])
                max_vol = self.known_syringes[pump_args[0]]['max_volume']
                self.syringe_vol_gauge_high.SetLabel(str(max_vol))
                self.syringe_vol_gauge.SetRange(int(round(float(max_vol)*1000)))

            if len(pump_args) >= 2:
                self.pump_address.SetValue(pump_args[1])

            if len(pump_args) >= 3:
                self.dual_syringe.SetStringSelection(str(pump_args[2]))

        elif pump_type == 'Soft Syringe':
            if 'syringe' in pump_kwargs.keys():
                self.syringe_type2.SetStringSelection(pump_kwargs['syringe'])

            if len(pump_args) >=1:
                self.syringe_type2.SetStringSelection(pump_args[0])
                max_vol = self.known_syringes[pump_args[0]]['max_volume']
                self.syringe_vol_gauge_high.SetLabel(str(max_vol))
                self.syringe_vol_gauge.SetRange(int(round(float(max_vol)*1000)))

        if pump_type in my_pumps and comport in self.all_comports:
            logger.info('Initialized pump %s on startup', self.name)
            self._connect()

        elif pump_type == 'Soft' or pump_type == 'Soft Syringe':
            logger.info('Initialized pump %s on startup', self.name)
            self._connect()

    def _on_type(self, evt):
        """Called when the pump type is changed in the GUI."""
        pump = self.type_ctrl.GetStringSelection()
        logger.info('Changed the pump type to %s for pump %s', pump, self.name)

        if pump == 'VICI M50':
            self.settings_box_sizer.Show(self.m50_settings_sizer, recursive=True)
            self.settings_box_sizer.Hide(self.phd4400_settings_sizer, recursive=True)
            self.settings_box_sizer.Hide(self.soft_syringe_settings_sizer, recursive=True)
            self.pump_mode = 'continuous'
        elif pump == 'PHD 4400' or pump == 'NE 500':
            self.settings_box_sizer.Hide(self.m50_settings_sizer, recursive=True)
            self.settings_box_sizer.Show(self.phd4400_settings_sizer, recursive=True)
            self.settings_box_sizer.Hide(self.soft_syringe_settings_sizer, recursive=True)
            self.pump_mode = 'syringe'
        elif pump == 'Soft':
            self.settings_box_sizer.Hide(self.m50_settings_sizer, recursive=True)
            self.settings_box_sizer.Hide(self.phd4400_settings_sizer, recursive=True)
            self.settings_box_sizer.Hide(self.soft_syringe_settings_sizer, recursive=True)
            self.pump_mode = 'continuous'
        elif pump == 'Soft Syringe':
            self.settings_box_sizer.Hide(self.m50_settings_sizer, recursive=True)
            self.settings_box_sizer.Hide(self.phd4400_settings_sizer, recursive=True)
            self.settings_box_sizer.Show(self.soft_syringe_settings_sizer, recursive=True)
            self.pump_mode = 'syringe'

        if self.pump_mode == 'continuous':
            self.status_sizer.Hide(self.vol_gauge, recursive=True)
            self.syringe_volume.Hide()
            self.syringe_volume_units.Hide()
            self.syringe_volume_label.Hide()
            self.set_syringe_volume.Hide()
            self.refill_rate_ctrl.Hide()
            self.refill_rate_lbl.Hide()
            self.refill_rate_units.Hide()
        else:
            self.status_sizer.Show(self.vol_gauge, recursive=True)
            self.syringe_volume.Show()
            self.syringe_volume_units.Show()
            self.syringe_volume_label.Show()
            self.set_syringe_volume.Show()
            self.refill_rate_ctrl.Show()
            self.refill_rate_lbl.Show()
            self.refill_rate_units.Show()

        self.Layout()

    def _on_units(self, evt):
        """Called when the units are changed in the GUI."""
        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()

        old_units = self.flow_units_lbl.GetLabel()

        self.flow_units_lbl.SetLabel('{}/{}'.format(vol_unit, t_unit))
        self.vol_units_lbl.SetLabel(vol_unit)
        self.syringe_volume_units.SetLabel(vol_unit)
        self.refill_units_lbl.SetLabel('{}/{}'.format(vol_unit, t_unit))

        try:
            flow_rate = float(self.flow_rate_ctrl.GetValue())
        except ValueError:
            flow_rate = 0

        old_vol, old_t = old_units.split('/')

        if old_vol != vol_unit:
            if (old_vol == 'nL' and vol_unit == 'uL') or (old_vol == 'uL' and vol_unit == 'mL'):
                flow_rate = flow_rate/1000.
            elif old_vol == 'nL' and vol_unit == 'mL':
                flow_rate = flow_rate/1000000.
            elif (old_vol == 'mL' and vol_unit == 'uL') or (old_vol == 'uL' and vol_unit == 'nL'):
                flow_rate = flow_rate*1000.
            elif old_vol == 'mL' and vol_unit == 'nL':
                flow_rate = flow_rate*1000000.
        if old_t != t_unit:
            if old_t == 'min':
                flow_rate = flow_rate/60
            else:
                flow_rate = flow_rate*60

        if flow_rate != 0:
            self.flow_rate_ctrl.ChangeValue('{0:.3f}'.format(flow_rate))

        try:
            refill_rate = float(self.refill_rate_ctrl.GetValue())
        except ValueError:
            refill_rate = 0

        old_vol, old_t = old_units.split('/')

        if old_vol != vol_unit:
            if (old_vol == 'nL' and vol_unit == 'uL') or (old_vol == 'uL' and vol_unit == 'mL'):
                refill_rate = refill_rate/1000.
            elif old_vol == 'nL' and vol_unit == 'mL':
                refill_rate = refill_rate/1000000.
            elif (old_vol == 'mL' and vol_unit == 'uL') or (old_vol == 'uL' and vol_unit == 'nL'):
                refill_rate = refill_rate*1000.
            elif old_vol == 'mL' and vol_unit == 'nL':
                refill_rate = refill_rate*1000000.
        if old_t != t_unit:
            if old_t == 'min':
                refill_rate = refill_rate/60
            else:
                refill_rate = refill_rate*60

        if refill_rate != 0:
            self.refill_rate_ctrl.ChangeValue('{0:.3f}'.format(refill_rate))

        logger.debug('Changed the pump units to %s and %s for pump %s', vol_unit, t_unit, self.name)

    def _on_mode(self, evt):
        """Called when the flow mode is changed in the GUI"""
        mode = self.mode_ctrl.GetStringSelection()

        if mode == 'Continuous flow':
            self.volume_lbl.Hide()
            self.volume_ctrl.Hide()
            self.vol_units_lbl.Hide()
        else:
            self.volume_lbl.Show()
            self.volume_ctrl.Show()
            self.vol_units_lbl.Show()

        logger.debug('Changed the pump mode to %s for pump %s', mode, self.name)

    def _on_run(self, evt):
        """Called when flow is started or stopped in the GUI."""
        if self.connected:
            if self.run_button.GetLabel() == 'Start':
                fr_set = self._set_flowrate()
                if not fr_set:
                    return

                mode = self.mode_ctrl.GetStringSelection()
                if mode == 'Fixed volume':
                    try:
                        vol = float(self.volume_ctrl.GetValue())
                    except Exception:
                        msg = "Volume must be a number."
                        wx.MessageBox(msg, "Error setting volume")
                        logger.debug('Failed to set dispense/aspirate volume to %s for pump %s', vol, self.name)
                        return

                logger.info('Starting pump %s flow', self.name)
                if self.pump_mode == 'continuous':
                    if mode == 'Fixed volume':
                        cmd = self.direction_ctrl.GetStringSelection().lower()
                        self._send_cmd(cmd)
                        self._set_status(cmd.capitalize())
                    else:
                        self._send_cmd('start_flow')
                        self._set_status('Flowing')
                else:
                    if mode == 'Fixed volume':
                        cmd = self.direction_ctrl.GetStringSelection().lower()
                        self._send_cmd(cmd)
                        self._set_status(cmd.capitalize())
                    else:
                        direction = self.direction_ctrl.GetStringSelection().lower()
                        cmd = '{}_all'.format(direction)
                        self._send_cmd(cmd)
                        self._set_status(direction.capitalize())

                self.fr_button.Show()
                self.run_button.SetLabel('Stop')

            else:
                logger.info('Stopping pump %s flow', self.name)
                self._send_cmd('stop')

                self.run_button.SetLabel('Start')
                self.fr_button.Hide()
                self.monitor_flow_evt.clear()

                self._set_status('Done')

        else:
            msg = "Cannot start pump flow before the pump is connected."
            wx.MessageBox(msg, "Error starting flow")
            logger.debug('Failed to start flow for pump %s because it is not connected', self.name)

    def _on_fr_change(self, evt):
        """Called when the flow rate is started or stopped in the GUI."""
        self._set_flowrate()

    def _on_connect(self, evt):
        """Called when a pump is connected in the GUI."""
        self._connect()

    def _on_set_volume(self, evt):
        wx.CallAfter(self._set_volume)

    def _set_volume(self):
        vol = wx.GetTextFromUser("Enter current syringe volume:",
            "Set Syringe Volume", "0", parent=self)

        try:
            vol = float(vol)
            if vol != -1:
                self.pump.volume = vol

            self._get_volume()

        except ValueError:
            msg = "Volume must be a number."
            wx.MessageBox(msg, "Error setting volume")


    def _on_syringe_type(self, evt):
        syringe_type = evt.GetEventObject()

        if self.connected:
            self._send_cmd('set_pump_cal')

        max_vol = self.known_syringes[syringe_type.GetStringSelection()]['max_volume']
        self.syringe_vol_gauge_high.SetLabel(str(max_vol))
        self.syringe_vol_gauge.SetRange(int(round(float(max_vol)*1000)))

    def _connect(self):
        """Initializes the pump in the PumpCommThread"""
        pump = self.type_ctrl.GetStringSelection().replace(' ', '_')

        if pump == 'VICI_M50':
            try:
                fc = float(self.m50_fcal.GetValue())
                bc = float(self.m50_bcal.GetValue())
            except Exception:
                msg = "Calibration values must be numbers."
                wx.MessageBox(msg, "Error setting calibration values")
                logger.debug('Failed to connect to pump %s because the M50 calibration values were bad', self.name)
                return

        com = self.com_ctrl.GetStringSelection()
        pump = self.type_ctrl.GetStringSelection().replace(' ', '_')

        if pump == 'VICI_M50':
            kwargs = {'flow_cal': fc, 'backlash_cal': bc, 'comm_lock': self.comm_lock}
        elif pump == 'PHD_4400' or pump == 'NE_500':
            kwargs = copy.deepcopy(self.known_syringes[self.syringe_type.GetStringSelection()])
            kwargs['comm_lock'] = self.comm_lock
            kwargs['syringe_id'] = self.syringe_type.GetStringSelection()
            kwargs['pump_address'] = self.pump_address.GetValue()
            kwargs['dual_syringe'] = self.dual_syringe.GetStringSelection() == 'True'
        elif pump == 'Soft_Syringe':
            kwargs = copy.deepcopy(self.known_syringes[self.syringe_type2.GetStringSelection()])
            kwargs['syringe_id'] = self.syringe_type2.GetStringSelection()
        else:
            kwargs = {}

        try:
            self.pump = self.known_pumps[pump](com, self.name, **kwargs)
            self._set_status('Connected')
            self._send_cmd('add_pump')
        except Exception as e:
            logger.error(e)
            self._set_status('Connection Failed')
            return

        start_time = time.time()
        while len(self.answer_q) == 0 and time.time()-start_time < 5:
            time.sleep(0.01)

        if len(self.answer_q) > 0:
            self.answer_q.popleft()

        logger.info('Connected to pump %s', self.name)
        self.connected = True
        self.connect_button.SetLabel('Reconnect')

        return

    def start_callback(self):
        self.monitor_flow_evt.set()

    def _get_volume(self):
        """Initializes the pump in the PumpCommThread"""
        # self.comm_lock.acquire()
        volume = self.pump.volume
        # self.comm_lock.release()

        wx.CallAfter(self._set_status_volume, volume)
        wx.CallAfter(self.syringe_vol_gauge.SetValue,
            int(round(float(volume)*1000)))

    def _get_volume_delay(self, delay):
        wx.CallLater(delay*1000, self._get_volume)

    def _set_status(self, status):
        """
        Changes the status in the GUI.

        :param str status: The status to display.
        """
        logger.debug('Setting pump %s status to %s', self.name, status)
        self.status.SetLabel(status)

    def _set_status_volume(self, volume):
        logger.debug("Setting pump %s volume to %s", self.name, volume)
        self.syringe_volume.SetLabel('{}'.format(round(float(volume), 3)))

    def _set_flowrate(self):
        """
        Sets the flowrate for the pump.

        :returns: ``True`` if the flow rate is set successfully, ``False`` otherwise.
        :rtype: bool
        """
        self._send_cmd('set_units')

        if self.type_ctrl.GetStringSelection() == 'NE 500':
            if self.direction_ctrl.GetStringSelection() == 'Dispense':
                try:
                    fr = float(self.flow_rate_ctrl.GetValue())
                    self._send_cmd('set_flow_rate')
                    success = True
                    logger.debug('Set pump %s flow rate to %s', self.name, str(fr))
                except Exception:
                    msg = "Flow rate must be a number."
                    wx.MessageBox(msg, "Error setting flow rate")
                    success = False
                    logger.debug('Failed to set pump %s flow rate', self.name)

            else:
                try:
                    fr = float(self.refill_rate_ctrl.GetValue())
                    self._send_cmd('set_refill_rate')
                    success = True
                    logger.debug('Set pump %s flow rate to %s', self.name, str(fr))
                except Exception:
                    msg = "Refill rate must be a number."
                    wx.MessageBox(msg, "Error setting refill rate")
                    success = False
                    logger.debug('Failed to set pump %s refill rate', self.name)

        else:
            try:
                fr = float(self.flow_rate_ctrl.GetValue())
                self._send_cmd('set_flow_rate')
                success = True
                logger.debug('Set pump %s flow rate to %s', self.name, str(fr))
            except Exception:
                msg = "Flow rate must be a number."
                wx.MessageBox(msg, "Error setting flow rate")
                success = False
                logger.debug('Failed to set pump %s flow rate', self.name)

            if success and self.pump_mode == 'syringe':
                try:
                    fr = float(self.refill_rate_ctrl.GetValue())
                    self._send_cmd('set_refill_rate')
                    success = True
                    logger.debug('Set pump %s flow rate to %s', self.name, str(fr))
                except Exception:
                    msg = "Refill rate must be a number."
                    wx.MessageBox(msg, "Error setting refill rate")
                    success = False
                    logger.debug('Failed to set pump %s refill rate', self.name)

        return success

    def _monitor_flow(self):
        """
        Called every second when the pump is moving in fixed volume mode.
        It checks the pump status, and if it is done moving it updates the GUI
        status.
        """
        while True:
            self.monitor_flow_evt.wait()
            # self.comm_lock.acquire()
            is_moving = self.pump.is_moving()
            # self.comm_lock.release()

            if not is_moving:
                wx.CallAfter(self.run_button.SetLabel, 'Start')
                wx.CallAfter(self.fr_button.Hide)
                wx.CallAfter(self._set_status, 'Done')
                self.monitor_flow_evt.clear()

                if self.pump_mode == 'syringe':
                    self._send_cmd('stop')

            if self.pump_mode == 'syringe':
                self._get_volume()

            time.sleep(1)

            if not is_moving and self.pump_mode == 'syringe':
                wx.CallAfter(self._get_volume_delay, 2)

    def _send_cmd(self, cmd, args=None):
        """
        Sends commands to the pump using the ``pump_cmd_q`` that was given
        to :py:class:`PumpCommThread`.

        :param str cmd: The command to send, matching the command in the
            :py:class:`PumpCommThread` ``_commands`` dictionary.
        """
        logger.debug('Sending pump %s command %s', self.name, cmd)
        if cmd == 'is_moving':
            self.pump_cmd_q.append(('is_moving', (self.name), {}))
        elif cmd == 'start_flow':
            self.pump_cmd_q.append(('start_flow', (self.name, self.start_callback), {}))
        elif cmd == 'stop':
            self.pump_cmd_q.append(('stop', (self.name,), {}))
        elif cmd == 'dispense':
            units = self.flow_units_lbl.GetLabel()
            vol = float(self.volume_ctrl.GetValue())
            self.pump_cmd_q.append(('dispense', (self.name, vol, self.start_callback, units), {}))
        elif cmd == 'aspirate':
            units = self.flow_units_lbl.GetLabel()
            vol = float(self.volume_ctrl.GetValue())
            self.pump_cmd_q.append(('aspirate', (self.name, vol, self.start_callback, units), {}))
        elif cmd == 'dispense_all':
            self.pump_cmd_q.append(('dispense_all', (self.name, self.start_callback), {}))
        elif cmd == 'aspirate_all':
            self.pump_cmd_q.append(('aspirate_all', (self.name, self.start_callback), {}))
        elif cmd == 'set_flow_rate':
            direction = self.direction_ctrl.GetStringSelection().lower()
            if self.pump_mode == 'continuous':
                if direction == 'dispense':
                    mult = 1
                else:
                    mult = -1
            else:
                mult = 1
            fr = mult*float(self.flow_rate_ctrl.GetValue())
            self.pump_cmd_q.append(('set_flow_rate', (self.name, fr), {}))
        elif cmd == 'set_refill_rate':
            fr = float(self.refill_rate_ctrl.GetValue())
            self.pump_cmd_q.append(('set_refill_rate', (self.name, fr), {}))
        elif cmd == 'set_units':
            units = self.flow_units_lbl.GetLabel()
            self.pump_cmd_q.append(('set_units', (self.name, units), {}))
        elif cmd == 'set_volume':
            vol = args[0]
            self.pump_cmd_q.append(('set_volume', (self.name, vol), {}))
        elif cmd == 'get_volume':
            self.pump_cmd_q.append(('get_volume', (self.name,), {}))
        elif cmd == 'set_pump_cal':
            if self.type_ctrl.GetStringSelection() == 'Soft Syringe':
                syringe_type = self.syringe_type2
            else:
                syringe_type = self.syringe_type
            vals = copy.deepcopy(self.known_syringes[syringe_type.GetStringSelection()])
            vals['syringe_id'] = syringe_type.GetStringSelection()
            self.pump_cmd_q.append(('set_pump_cal', (self.name,), vals))
        elif cmd == 'connect':
            com = self.com_ctrl.GetStringSelection()
            pump = self.type_ctrl.GetStringSelection().replace(' ', '_')

            args = (com, self.name, pump)

            if pump == 'VICI_M50':
                fc = float(self.m50_fcal.GetValue())
                bc = float(self.m50_bcal.GetValue())
                kwargs = {'flow_cal': fc, 'backlash_cal': bc, 'comm_lock': self.comm_lock}
            elif pump == 'PHD_4400' or pump == 'NE_500':
                kwargs = self.known_syringes[self.syringe_type.GetStringSelection()]
                kwargs['comm_lock'] = self.comm_lock
                kwargs['syringe_id'] = self.syringe_type.GetStringSelection()
                kwargs['pump_address'] = self.pump_address.GetValue()
            elif pump == 'Soft_Syringe':
                kwargs = self.known_syringes[self.syringe_type2.GetStringSelection()]
                kwargs['syringe_id'] = self.syringe_type2.GetStringSelection()
            else:
                kwargs = {}

            self.pump_cmd_q.append(('connect', args, kwargs))
        elif cmd == 'add_pump':
            args = (self.pump, self.name)

            self.pump_cmd_q.append(('add_pump', args, {}))


class PumpFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, comm_locks, setup_pumps, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(PumpFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the PumpFrame')
        self.pump_cmd_q = deque()
        self.pump_answer_q = deque()
        self.abort_event = threading.Event()
        self.pump_con = PumpCommThread(self.pump_cmd_q, self.pump_answer_q, self.abort_event, 'PumpCon')
        self.pump_con.start()

        self.comm_locks = comm_locks

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._get_ports()

        self.pumps =[]

        top_sizer = self._create_layout()

        self.SetSizer(top_sizer)

        self.Fit()
        self.Raise()

        self._initpumps(setup_pumps)

    def _create_layout(self):
        """Creates the layout"""
        self.top_panel = wx.Panel(self)

        pump_panel = PumpPanel(self.top_panel, wx.ID_ANY, 'stand_in', self.ports,
            self.pump_cmd_q, self.pump_answer_q, self.pump_con.known_pumps,
            'stand_in')

        self.pump_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.pump_sizer.Add(pump_panel, flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN)

        self.pump_sizer.Hide(pump_panel, recursive=True)

        button_panel = wx.Panel(self.top_panel)

        add_pump = wx.Button(button_panel, label='Add pump')
        add_pump.Bind(wx.EVT_BUTTON, self._on_addpump)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_pump)

        button_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        button_panel_sizer.Add(wx.StaticLine(button_panel), flag=wx.EXPAND|wx.TOP|wx.BOTTOM, border=2)
        button_panel_sizer.Add(button_sizer, flag=wx.ALIGN_RIGHT|wx.BOTTOM|wx.RIGHT, border=2)

        button_panel.SetSizer(button_panel_sizer)

        top_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        top_panel_sizer.Add(self.pump_sizer, flag=wx.EXPAND)
        top_panel_sizer.Add(button_panel, border=10, flag=wx.EXPAND|wx.TOP)

        self.top_panel.SetSizer(top_panel_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.top_panel, flag=wx.EXPAND, proportion=1)

        return top_sizer

    def _initpumps(self, setup_pumps):
        """
        This is a convenience function for initalizing pumps on startup, if you
        already know what pumps you want to add. You can comment it out in
        the ``__init__`` if you want to not load any pumps on startup.

        If you want to add pumps here, add them to the ``setup_pumps`` list.
        Each entry should be an iterable with the following parameters: name,
        pump type, comport, pump arg list, pump kwarg dict, and pump panel
        kwarg dict in that order. How the arg list and kwarg dict are handled
        are defined in the :py:func:`PumpPanel._initpump` function, and depends
        on the pump type.
        """
        if not self.pumps:
            self.pump_sizer.Remove(0)

        if setup_pumps is None:
            setup_pumps = [('Sheath', 'VICI M50', 'COM3', ['629.88', '13.381'], {}, {}),
                        ('Outlet', 'VICI M50', 'COM4', ['626.36', '10.109'], {}, {})
                        ]

            # setup_pumps = [
            #         # ('Sample', 'PHD 4400', '/dev/ttyUSB6', ['30 mL, EXEL', '2'], {},
            #         # {'flow_rate' : '30', 'refill_rate' : '30'}),
            #         ('Buffer', 'PHD 4400', '/dev/ttyUSB6', ['30 mL, EXEL', '1'], {},
            #         {'flow_rate' : '30', 'refill_rate' : '30'}),
            #         # ('3', 'PHD 4400', 'COM4', ['30 mL, EXEL', '3'], {},
            #         # {'flow_rate' : '30', 'refill_rate' : '30'}),
            #             ]

            # setup_pumps = [
            #     ('Sample', 'PHD 4400', 'COM4', ['10 mL, Medline P.C.', '1'], {},
            #         {'flow_rate' : '10', 'refill_rate' : '10'}),
            #     ('Buffer 1', 'PHD 4400', 'COM4', ['20 mL, Medline P.C.', '2'], {},
            #         {'flow_rate' : '10', 'refill_rate' : '10'}),
            #     ('Buffer 2', 'PHD 4400', 'COM4', ['20 mL, Medline P.C.', '3'], {},
            #         {'flow_rate' : '10', 'refill_rate' : '10'}),
            #     ]

            # setup_pumps = [
            #     ('Buffer', 'NE 500', 'COM11', ['20 mL, Medline P.C.', '00'],
            #         {'dual_syringe': 'False'}, {'flow_rate' : '0.1', 'refill_rate' : '10'}),
            #     ('Sheath', 'NE 500', 'COM10', ['20 mL, Medline P.C.', '01'],
            #         {'dual_syringe': 'False'}, {'flow_rate' : '0.1', 'refill_rate' : '10'}),
            #     ('Sample', 'NE 500', 'COM3', ['20 mL, Medline P.C.', '02'], {},
            #         {'flow_rate' : '0.1', 'refill_rate' : '10'}),
            #     ]

            setup_pumps = [
                ('Buffer 1', 'PHD 4400', 'COM4', ['20 mL, Medline P.C.', '1'], {},
                    {'flow_rate' : '10', 'refill_rate' : '10'}),
                ('Buffer 2', 'PHD 4400', 'COM4', ['20 mL, Medline P.C.', '2'], {},
                    {'flow_rate' : '10', 'refill_rate' : '10'}),
                ('Sheath', 'NE 500', 'COM10', ['20 mL, Medline P.C.', '01'],
                    {'dual_syringe': 'False'}, {'flow_rate' : '0.1', 'refill_rate' : '10'}),
                ('Sample', 'PHD 4400', 'COM4', ['20 mL, Medline P.C.', '3'], {},
                    {'flow_rate' : '10', 'refill_rate' : '10'}),
                ]

            # setup_pumps = [
            #     ('Sample', 'NE 500', '/dev/cu.usbserial-AK06V22M', ['30 mL, EXEL', '02', False], {},
            #         {'flow_rate' : '30', 'refill_rate' : '30'}),
            #     ('Sheath', 'NE 500', '/dev/cu.usbserial-A6022U62', ['30 mL, EXEL', '01', True], {},
            #         {'flow_rate' : '30', 'refill_rate' : '30'}),
            #     ('Buffer', 'NE 500', '/dev/cu.usbserial-A6022U22', ['30 mL, EXEL', '00', True], {},
            #         {'flow_rate' : '30', 'refill_rate' : '30'}),
            #     ]

            # setup_pumps = [('Sheath', 'Soft Syringe', '',
            #     ['10 mL, Medline P.C.',], {}, {'flow_rate' : '10',
            #     'refill_rate' : '10'}),
                        # ]

            # setup_pumps = [('Pump 2', 'VICI M50', 'COM6', ['626.8', '11.935'], {}, {}),
                        # ]

        logger.info('Initializing %s pumps on startup', str(len(setup_pumps)))

        for pump in setup_pumps:
            self._add_pump(pump)

        self.Layout()
        self.Fit()

    def _on_addpump(self, evt):
        """
        Called when the Add pump button is used. Adds a new pump to the control
        panel.

        .. note:: Pump names must be distinct.
        """
        if not self.pumps:
            self.pump_sizer.Remove(0)

        dlg = wx.TextEntryDialog(self, "Enter pump name:", "Create new pump")
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetValue()
            for pump in self.pumps:
                if name == pump.name:
                    msg = "Pump names must be distinct. Please choose a different name."
                    wx.MessageBox(msg, "Failed to add pump")
                    logger.debug('Attempted to add a pump with the same name (%s) as another pump.', name)
                    return

            pump_vals = (name, None, None, [], {}, {})
            self._add_pump(pump_vals)
            logger.info('Added new pump %s to the pump control panel.', name)

            self.Layout()
            self.Fit()

        return

    def _add_pump(self, pump):
        if pump[0] in self.comm_locks:
            comm_lock = self.comm_locks[pump[0]]
            new_pump = PumpPanel(self.top_panel, wx.ID_ANY, pump[0], self.ports, self.pump_cmd_q,
                self.pump_answer_q, self.pump_con.known_pumps, pump[0], pump[1],
                pump[2], pump[3], pump[4], comm_lock, **pump[5])
        else:
            logger.info('creating new comlock!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!')
            comm_lock = threading.Lock()
            self.comm_locks[pump[0]] = comm_lock
            new_pump = PumpPanel(self.top_panel, wx.ID_ANY, pump[0], self.ports, self.pump_cmd_q,
                self.pump_answer_q, self.pump_con.known_pumps, pump[0], pump[1],
                pump[2], pump[3], pump[4], comm_lock, **pump[5])

        self.pump_sizer.Add(new_pump, border=5, flag=wx.LEFT|wx.RIGHT)
        self.pumps.append(new_pump)

    def _get_ports(self):
        """
        Gets a list of active comports.

        .. note:: This doesn't update after the program is opened, so you need
            to start the program after all pumps are connected to the computer.
        """
        port_info = list_ports.comports()
        self.ports = [port.device for port in port_info]

        # if platform.system() == 'Darwin':
        #     for i in range(len(self.ports)):
        #         self.ports[i] = self.ports[i].replace('/cu.', '/tty.', 1)

        logger.debug('Found the following comports for the PumpFrame: %s', ' '.join(self.ports))

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the PumpFrame')
        self.pump_con.stop()
        self.pump_con.join()
        while self.pump_con.is_alive():
            time.sleep(0.001)
        self.Destroy()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.DEBUG)
    h1.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    # my_pump = M50Pump('COM6', '2')
    # comm_lock = threading.Lock()

    # my_pump = PHD4400Pump('COM4', 'H1', '1', 23.5, 30, 30, '30 mL', comm_lock)
    # my_pump.flow_rate = 10
    # my_pump.refill_rate = 10

    # my_pump2 = PHD4400Pump('COM4', 'H2', '2', 23.5, 30, 30, '30 mL', comm_lock)
    # my_pump2.flow_rate = 10
    # my_pump2.refill_rate = 10

    # my_pump = NE500Pump('/dev/cu.usbserial-A6022U22', 'Pump2', '00', 23.5, 30, 30, '30 mL', comm_lock)
    # my_pump.flow_rate = 10
    # my_pump.refill_rate = 10

    # pmp_cmd_q = deque()
    # return_q = queue.Queue()
    # abort_event = threading.Event()
    # my_pumpcon = PumpCommThread(pmp_cmd_q, return_q, abort_event, 'PumpCon')
    # my_pumpcon.start()

    # init_cmd = ('connect', ('COM6', 'pump2', 'VICI_M50'),
    #     {'flow_cal': 626.2, 'backlash_cal': 9.278})
    # fr_cmd = ('set_flow_rate', ('pump2', 2000), {})
    # start_cmd = ('start_flow', ('pump2',), {})
    # stop_cmd = ('stop', ('pump2',), {})
    # dispense_cmd = ('dispense', ('pump2', 200), {})
    # aspirate_cmd = ('aspirate', ('pump2', 200), {})
    # moving_cmd = ('is_moving', ('pump2', return_q), {})

    # pmp_cmd_q.append(init_cmd)
    # pmp_cmd_q.append(fr_cmd)
    # pmp_cmd_q.append(start_cmd)
    # pmp_cmd_q.append(dispense_cmd)
    # pmp_cmd_q.append(aspirate_cmd)
    # pmp_cmd_q.append(moving_cmd)
    # time.sleep(5)
    # pmp_cmd_q.append(stop_cmd)
    # my_pumpcon.stop()

    # #Use this with PHD 4400
    comm_lock = threading.Lock()

    comm_locks = {'Sample'   : comm_lock,
        'Buffer 1' : comm_lock,
        'Buffer 2' : comm_lock,
        }

    # #Use this with M50s
    # comm_locks = {'Sheath' : threading.Lock(),
    #     'Outlet' : threading.Lock(),
    #     }

    #Otherwise use this:
    # comm_locks = {}

    app = wx.App()
    logger.debug('Setting up wx app')
    frame = PumpFrame(comm_locks, None, None, title='Pump Control')
    frame.Show()
    app.MainLoop()


