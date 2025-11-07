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
import datetime
import ctypes
import os

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import serial
import serial.tools.list_ports as list_ports
import wx
from six import string_types

sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_07_02\\DLL64\\DLL64') #add the path of the library here
sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_07_02\\python_64')#add the path of the LoadElveflow.py
sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_07_02\\DLL32\\DLL32') #add the path of the library here
sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_07_02\\python_32')#add the path of the LoadElveflow.py

# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_08_06\\DLL\\DLL64') #add the path of the library here
# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_08_06\\DLL\\Python\\Python_64')#add the path of the LoadElveflow.py
# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_08_06\\DLL\\DLL32') #add the path of the library here
# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_08_06\\DLL\\Python\\Python_32')#add the path of the LoadElveflow.py

# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_09_04\\DLL\\DLL64') #add the path of the library here
# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_09_04\\DLL\\Python\\Python_64')#add the path of the LoadElveflow.py
# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_09_04\\DLL\\DLL32') #add the path of the library here
# sys.path.append('C:\\Users\\biocat\\Elveflow_SDK_V3_09_04\\DLL\\Python\\Python_32')#add the path of the LoadElveflow.py

try:
    import Elveflow64 as Elveflow
except Exception:
    try:
        import Elveflow32 as Elveflow
    except Exception:
        pass

import fmcon
import utils
import pid

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
            self.ser = serial.Serial(port, baudrate, bytesize, parity,
                stopbits, timeout, xonxoff, rtscts, write_timeout, dsrdtr,
                inter_byte_timeout, exclusive)
            logger.info("Connected to serial device on port %s", port)
        except ValueError:
            logger.exception("Failed to connect to serial device on port %s",
                port)
        except serial.SerialException:
            logger.exception("Failed to connect to serial device on port %s",
                port)
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

        logger.debug("Read %i bytes from serial device on port %s", size,
            self.ser.port)
        logger.debug("Serial device on port %s returned %s", self.ser.port,
            ret.decode('utf-8'))

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

        logger.debug("Read all waiting bytes from serial device on port %s",
            self.ser.port)
        logger.debug("Serial device on port %s returned %s", self.ser.port,
            ret.decode('utf-8'))

        return ret.decode('utf-8')

    def write(self, data, get_response=False, send_term_char = '\r\n',
        term_char='>'):
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
        logger.debug("Sending '%s' to serial device on port %s", data,
            self.ser.port)
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
            logger.exception("Failed to write '%s' to serial device on port %s",
                data, self.ser.port)

        logger.debug("Recived '%r' after writing to serial device on port %s",
            out, self.ser.port)

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

class PicoPlusSerialComm(SerialComm):
    """
    This class subclases ``SerialComm`` to handle PicoPlus specific
    quirks.
    """

    def write(self, data, pump_address, get_response=False, send_term_char = '\r\n',
        term_chars=[':', '>', '<', '*', ':T*'], timeout=5):
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

        possible_term = ['\r\n{:02d}{}'.format(pump_address, char) for char in term_chars]
        alt_possible_term = ['\n{:02d}{}'.format(pump_address, char) for char in term_chars]
        try:
            with self.ser as s:
                start_time = time.time()
                s.write(data)
                if get_response:
                    got_resp = False
                    while not got_resp:
                        if s.in_waiting > 0:
                            ret = s.read(s.in_waiting)
                            out += ret.decode('ascii')
                            logger.debug(out)
                            for term in possible_term:
                                if out.endswith(term):
                                    got_resp = True
                                    break
                        else:
                            for term in alt_possible_term:
                                if out.endswith(term):
                                    got_resp = True
                                    break

                        if time.time() - start_time > timeout:
                            logger.error('Timed out waiting for a response on port %s', self.ser.port)
                            break

                        time.sleep(.001)
        except ValueError:
            logger.exception("Failed to write '%s' to serial device on port %s", data, self.ser.port)
        except Exception:
            logger.error("Failed to write to serial port!")
        logger.debug("Recived '%s' after writing to serial device on port %s", out, self.ser.port)

        return out

class LongerSerialComm(SerialComm):
    """
    This class subclases ``SerialComm`` to handle MForce specific
    errors.
    """

    def write(self, data, pump_addr, get_response=True):
        """
        This warps the Serial.write() function. It encodes the input
        data if necessary. It can return any expected response from the
        controller.

        :param data: Data to be written to the serial device.
        :type data: bytes

        :param term_char: The terminal character expected in a response
        :type term_char: str

        :returns: The requested response, or an empty string
        :rtype: str
        """
        logger.debug("Sending %r to serial device on port %s", data, self.ser.port)

        out = bytearray()
        timeout = 1
        start_time = time.time()
        try:
            with self.ser as s:
                s.write(data)
                if get_response:
                    resp_len = 0
                    while time.time()-start_time<timeout:
                        if s.in_waiting > 0:
                            ret = s.read(s.in_waiting)
                            out += ret

                        if len(out) >= 2 and resp_len == 0:
                            resp = out.hex(' ')
                            resp_len = int(resp.split()[1], 16)

                        if len(out) >= resp_len and resp_len != 0:
                            break
                        time.sleep(.001)
        except ValueError:
            logger.exception("Failed to write %r to serial device on port %s", data, self.ser.port)

        out = out.hex(' ')
        logger.debug("Recived %r after writing to serial device on port %s", out, self.ser.port)

        return out

def convert_volume(volume, u1, u2):
    if u1.lower() in ['nl', 'ul', 'ml'] and u2.lower() in ['nl', 'ul', 'ml']:
        if u1.lower() != u2.lower():
            if ((u1.lower() == 'nl' and u2.lower() == 'ul')
                or (u1.lower() == 'ul' and u2.lower() == 'ml')):
                volume = volume/1000.
            elif u1.lower() == 'nl' and u2.lower() == 'ml':
                volume = volume/1000000.
            elif ((u1.lower() == 'ml' and u2.lower() == 'ul')
                or (u1.lower() == 'ul' and u2.lower() == 'nl')):
                volume = volume*1000.
            elif u1.lower() == 'ml' and u2.lower() == 'nl':
                volume = volume*1000000.

    return volume

def convert_time(time, u1, u2):
    if u1.lower() in ['s', 'min'] and u2.lower() in ['s', 'min']:
        if u1.lower() != u2.lower():
            if u1.lower() == 'min':
                time = time/60
            else:
                time = time*60

    return time

def convert_flow_rate(fr, u1, u2):
    v_u1, t_u1 = u1.split('/')
    v_u2, t_u2 = u2.split('/')

    fr = convert_volume(fr, v_u1, v_u2)
    fr = convert_time(fr, t_u1, t_u2)

    return fr

def convert_flow_accel(accel, u1, u2):
    v_u1, t_u1 = u1.split('/')
    v_u2, t_u2 = u2.split('/')

    accel = convert_volume(accel, v_u1, v_u2)
    accel = convert_time(accel, t_u1, t_u2)
    accel = convert_time(accel, t_u1, t_u2)

    return accel

def convert_pressure(pressure, u1, u2):
    if (u1.lower() in ['psi', 'mpa', 'bar', 'mbar']
        and u2.lower() in ['psi', 'mpa', 'bar', 'mbar']):

        if u1.lower() != u2.lower():
            if u1.lower() == 'psi' and u2.lower() == 'mpa':
                pressure = pressure/145.038
            elif u1.lower() == 'psi' and u2.lower() == 'bar':
                pressure = pressure/14.5038
            elif u1.lower() == 'psi' and u2.lower() == 'mbar':
                pressure = 1000*pressure/14.5038
            elif u1.lower() == 'mpa' and u2.lower() == 'psi':
                pressure = pressure*145.038
            elif u1.lower() == 'mpa' and u2.lower() == 'bar':
                pressure = pressure*10
            elif u1.lower() == 'mpa' and u2.lower() == 'mbar':
                pressure = 1000*pressure*10
            elif u1.lower() == 'bar' and u2.lower() == 'psi':
                pressure = pressure*14.5038
            elif u1.lower() == 'bar' and u2.lower() == 'mpa':
                pressure = pressure/10
            elif u1.lower() == 'bar' and u2.lower() == 'mbar':
                pressure = pressure*1000
            elif u1.lower() == 'mbar' and u2.lower() == 'psi':
                pressure = pressure*14.5038/1000
            elif u1.lower() == 'mbar' and u2.lower() == 'mpa':
                pressure = pressure/10/1000
            elif u1.lower() == 'mbar' and u2.lower() == 'bar':
                pressure = pressure/1000

    return pressure

class Pump(object):
    """
    This class contains the settings and communication for a generic pump.
    It is intended to be subclassed by other pump classes, which contain
    specific information for communicating with a given pump. A pump object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, name, device, flow_rate_scale=1, flow_rate_offset=0,
        scale_type='both', comm_lock=None):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """

        self.device = device
        self.name = name
        self.flow_rate_scale = flow_rate_scale
        self.flow_rate_offset = flow_rate_offset
        self.is_syringe_pump = False

        self.scale_type = scale_type
        #up, down, or both, indicating only scale up flowrate, only scale down
        # flow rate, or do both (e.g. if both scale and offset are set, can get
        # flow rates above or below set rate, but perhaps you only want to scale
        # up, and otherwise use the set rate)

        # Defines the base units that the pump hardware expects the flow rate
        # (and volume) in
        self._pump_base_units = 'mL/min'
        self._units = self._pump_base_units
        self._flow_rate = 0

        self._is_flowing = False #Wehther or not the pump is pumping, regardless of fixed/continuous or direction
        self._is_dispensing = False #Dispensing indicates outputing a fixed volume, in either direction
        self._flow_dir = 0

        if comm_lock is None:
            self.comm_lock = threading.Lock()
        else:
            self.comm_lock = comm_lock

        self.connected = False

        self.connect()


    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    def __str__(self):
        return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    def connect(self):
        if not self.connected:
            self.connected = True

        return self.connected

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

        if units in ['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min']:
            if units != old_units:
                self._units = units

                logger.info("Changed pump %s units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change pump %s units, units supplied were invalid: %s", self.name, units)

    def _convert_volume(self, volume, u1, u2):
        volume = convert_volume(volume, u1, u2)
        return volume

    def _convert_time(self, time, u1, u2):
        time = convert_time(time, u1, u2)
        return time

    def _convert_flow_rate(self, fr, u1, u2):
        fr = convert_flow_rate(fr, u1, u2)
        return fr

    def _convert_flow_accel(self, accel, u1, u2):
        accel = convert_flow_accel(accel, u1, u2)
        return accel

    def _convert_pressure(self, pressure, u1, u2):
        pressure = convert_pressure(pressure, u1, u2)
        return pressure

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
        return self._is_flowing

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

    def set_flow_rate_scale(self, scale):
        self.flow_rate_scale = scale

    def set_flow_rate_offset(self, offset):
        fro = self._convert_flow_rate(offset, self.units, self._pump_base_units)
        self.flow_rate_offset = fro

    def get_pressure(self):
        return None

    def get_flow_dir(self):
        return self._flow_dir

    def is_dispensing(self):
        return self._is_dispensing

    def get_valve_position(self):
        return None

    def stop(self):
        """Stops all pump flow."""
        pass #Should be implimented in each subclass

    def disconnect(self):
        """Close any communication connections"""
        self.connected = False


class SyringePump(Pump):
    """
    This class contains the settings and communication for a generic syringe pump.
    It is intended to be subclassed by other pump classes, which contain
    specific information for communicating with a given pump. A pump object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line.
    """

    def __init__(self, name, device, diameter, max_volume, max_rate,
        syringe_id, dual_syringe, flow_rate_scale=1, flow_rate_offset=0,
        scale_type='both', comm_lock=None):

        Pump.__init__(self, name, device, comm_lock=comm_lock)

        self._volume = 0
        self._max_volume = 0
        self._refill_rate = 0
        self.is_syringe_pump = True

        self.dual_syringe = dual_syringe

        # self.stop()
        self.set_pump_cal(diameter, max_volume, max_rate, syringe_id)

    @property
    def max_volume(self):
        max_volume = self._max_volume
        max_volume = self._convert_volume(max_volume,
            self._pump_base_units.split('/')[0], self.units.split('/')[0])

        return max_volume

    @max_volume.setter
    def max_volume(self, max_volume):
        max_volume = self._convert_volume(max_volume, self.units.split('/')[0],
            self._pump_base_units.split('/')[0])
        self._max_volume = max_volume

    def is_moving(self):
        old_move = copy.copy(self._is_flowing)

        moving = self._get_move_status()

        if not moving and old_move:
            vol = self._get_delivered_volume()

            if self._flow_dir > 0:
                self._volume = self._volume - vol
            elif self._flow_dir < 0:
                self._volume = self._volume + vol

        self._is_flowing = moving

        return moving

    @property
    def volume(self):
        flowing = self.is_moving()

        volume = self._volume

        if flowing:
            vol = self._get_delivered_volume()

            if self._flow_dir > 0:
                volume = volume - vol
            elif self._flow_dir < 0:
                volume = volume + vol

        volume = self._convert_volume(volume, self._pump_base_units.split('/')[0],
            self.units.split('/')[0])

        return volume

    @volume.setter
    def volume(self, volume):
        if self.is_moving():
            vol = self._get_delivered_volume()

            if self._flow_dir > 0:
                volume = volume + vol
            elif self._flow_dir < 0:
                volume = volume - vol

        volume = self._convert_volume(volume, self.units.split('/')[0],
            self._pump_base_units.split('/')[0])

        self._volume = volume

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

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s infuse flow rate to %f %s", self.name, rate, self.units)

        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

        self._flow_rate = self.round(rate)

        #Have to do this or can lose aspirate/dispense volume
        volume = self._volume

        if not self.is_moving():
            vol = self._get_delivered_volume()

            if self._flow_dir > 0:
                volume = volume - vol
            elif self._flow_dir < 0:
                volume = volume + vol

        self._volume = volume

        self._set_flow_rate()

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

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @refill_rate.setter
    def refill_rate(self, rate):
        logger.info("Setting pump %s refill flow rate to %f %s", self.name, rate, self.units)

        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

        self._refill_rate = self.round(rate)
        # logger.info('Checking volume')

        #Have to do this or can lose aspirate/dispense volume
        volume = self._volume

        if not self.is_moving():
            vol = self._get_delivered_volume()

            if self._flow_dir > 0:
                volume = volume - vol
            elif self._flow_dir < 0:
                volume = volume + vol

        self._volume = volume

        self._set_refill_rate()

    def dispense_all(self, blocking=True):
        if self._is_flowing:
            logger.debug("Stopping pump %s current motion before infusing", self.name)
            self.stop()

        vol = copy.copy(self.volume)

        if vol > 0:
            self.dispense(vol, self.units.split('/')[0],
                blocking=blocking)

    def dispense(self, vol, units='mL', blocking=True):
        """
        Dispenses a fixed volume.

        :param vol: Volume to dispense
        :type vol: float

        :param units: Volume units, defaults to mL, also accepts uL or nL
        :type units: str
        """
        orig_vol = copy.copy(vol)
        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

        if self._is_flowing:
            logger.debug("Stopping pump %s current motion before infusing", self.name)
            self.stop()

        cont = True

        if self.volume - vol < 0:
            logger.error(("Attempting to infuse {} mL, which is more than the "
                "current volume of the syringe ({} mL)".format(vol, self.volume)))
            cont = False

        vol = self.round(vol)

        if vol <= 0:
            logger.error(("Infuse volume must be positive."))
            cont = False

        if cont:

            logger.info("Pump %s infusing %f %s at %f %s", self.name, orig_vol, units,
                self.flow_rate, self.units)

            self._send_dispense_cmd(vol)

            self._flow_dir = 1

    def aspirate_all(self):
        if self._is_flowing:
            logger.debug("Stopping pump %s current motion before aspirating", self.name)
            self.stop()

        vol = copy.copy(self.volume)

        if self.round(self.max_volume - vol) > 0:
            self.aspirate(self.max_volume - vol,
                self.units.split('/')[0])
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
        orig_vol = copy.copy(vol)
        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

        if self._is_flowing:
            logger.debug("Stopping pump %s current motion before refilling", self.name)
            self.stop()

        cont = True

        if self.volume + vol > self.max_volume:
            logger.error(("Attempting to refill {} mL, which will take the total "
                "loaded volume to more than the maximum volume of the syringe "
                "({} mL)".format(vol, self.max_volume)))
            cont = False

        vol = self.round(vol)

        if vol <= 0:
            logger.error(("Refill volume must be positive."))
            cont = False

        if cont:
            logger.info("Pump %s refilling %f %s at %f %s", self.name, orig_vol, units,
                self.refill_rate, self.units)

            self._send_aspirate_cmd(vol)

            self._flow_dir = -1

    def stop(self):
        """Stops all pump flow."""
        logger.info("Pump %s stopping all motions", self.name)
        self._send_stop_cmd()

        if self._is_flowing:
            vol = self._get_delivered_volume()

            if self._flow_dir > 0:
                self._volume = self._volume - vol
            elif self._flow_dir < 0:
                self._volume = self._volume + vol

        self._is_flowing = False
        self._flow_dir = 0

    def set_pump_cal(self, diameter, max_volume, max_rate, syringe_id):
        self.diameter = diameter
        self.max_volume = max_volume
        self.max_rate = max_rate
        self.syringe_id = syringe_id

        self._send_pump_cal_cmd()

    def _get_move_status(self):
        # Pump specific, should return moving as True/False and set self._flow_dir
        pass

    def _set_flow_rate(self):
        # Pump specific, sets flow rate to self._flow_rate
        pass

    def _set_refill_rate(self):
        # Pump specific, sets refill rate to self._refill_rate
        pass

    def _get_delivered_volume(self):
        # Pump specific, returns delivered volume
        pass

    def _send_dispense_cmd(self, vol):
        # Pump specific, starts dispensing the volume vol
        pass

    def _send_aspirate_cmd(self, vol):
        # Pump specific, starts aspirating the volume vol
        pass

    def _send_stop_cmd(self):
        # Pump specific, sends pump stop command
        pass

    def _send_pump_cal_cmd(self):
        # Pump specific, sends pump calibration commands
        pass

    def round(self, val):
        # Overwrite if number of sig figs the pump commands accept is limited
        return val

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

    def __init__(self, name, device, comm_lock=None, flow_cal=628., backlash_cal=1.5):
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
        Pump.__init__(self, name, device, comm_lock=comm_lock)

        logstr = ("Initializing pump {} on serial port {}, flow "
            "calibration: {} uL/rev, backlash calibration: {} uL".format(self.name,
            self.device, flow_cal, backlash_cal))
        logger.info(logstr)

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

        self._units = 'mL/min'
        self._pump_base_units = 'uL/s'

        self._flow_cal = float(flow_cal)
        self._backlash_cal = float(backlash_cal)
        self._gear_ratio = 9.88 #Gear ratio provided by manufacturer, for M50 pumps

        self.cal = 200*256*self._gear_ratio/self._flow_cal #Calibration value in (micro)steps/uL
            #full steps/rev * microsteps/full step * gear ratio / uL/revolution = microsteps/uL

        ret = self.send_cmd('PR V', True) # Gets current flow rate, doesn't really work
        ret = ret.split('\r\n')[-2][-1]
        self._flow_rate = float(ret)
        self.is_moving()

    def connect(self):
        if not self.connected:
            with self.comm_lock:
                self.pump_comm = MForceSerialComm(self.device)

            self.connected = True

        return self.connected

    @property
    def flow_rate(self):
        rate = float(self._flow_rate)/self.cal

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s flow rate to %f %s", self.name, rate, self.units)

        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

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

        if self._is_flowing and not self._is_dispensing:
            self.send_cmd("SL {}".format(self._flow_rate))
        else:
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

        with self.comm_lock:
            ret = self.pump_comm.write(cmd, get_response)

        if get_response:
            logger.debug("Pump %s returned %r", self.name, ret)

        return ret


    def is_moving(self):
        status = self.send_cmd("PR MV")

        status = status.split('\r\n')[-2][-1]
        status = int(status)

        if status == 1:
            status = True
        else:
            status = False
            self._is_dispensing = False
            self._flow_dir = 0

        self._is_flowing = status

        logger.debug("Pump %s moving: %s", self.name, str(self._is_flowing))

        return self._is_flowing

    def start_flow(self):
        if self._is_flowing:
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
        if self._is_flowing:
            logger.debug("Stopping pump %s current motion before starting flow", self.name)
            self.stop()

        if vol > 0:
            logger.info("Pump %s dispensing %f %s at %f %s", self.name, vol, units,
                self.flow_rate, self.units)
        elif vol < 0:
            logger.info("Pump %s aspirating %f %s at %f %s", self.name, abs(vol),
                units, self.flow_rate, self.units)

        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

        if vol > 0 and self._flow_dir < 0:
            vol = vol + self._backlash_cal
            logger.debug("Pump %s added backlash correction for dispensing/aspirating", self.name)
        elif vol < 0 and self._flow_dir > 0:
            vol = vol - self._backlash_cal
            logger.debug("Pump %s added backlash correction for dispensing/aspirating", self.name)

        vol =int(round(vol*self.cal))

        self.send_cmd("VM {}".format(abs(self._flow_rate)))
        self.send_cmd("MR {}".format(vol))

        self._is_flowing = True
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


class PHD4400Pump(SyringePump):
    """
    Harvard PHD 4400 control
    """

    def __init__(self, name, device, pump_address, diameter, max_volume, max_rate,
        syringe_id, dual_syringe, comm_lock=None):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """
        self._pump_address = pump_address

        SyringePump.__init__(self, name, device, diameter, max_volume, max_rate,
            syringe_id, dual_syringe,comm_lock=comm_lock)

        logstr = ("Initializing PHD4400 pump {} on serial port {}".format(name, device))
        logger.info(logstr)

        self._units = 'mL/min'
        self._pump_base_units = 'mL/min'

        self.send_cmd('MOD VOL')

    def connect(self):
        if not self.connected:
            with self.comm_lock:
                self.pump_comm = PHD4400SerialComm(self.device,
                    stopbits=serial.STOPBITS_TWO, baudrate=19200)

            self.connected = True

        return self.connected

    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.
        """

        logger.debug("Sending pump %s cmd %r", self.name, cmd)

        with self.comm_lock:

            ret = self.pump_comm.write("{}{}".format(self._pump_address, cmd),
                self._pump_address, get_response=get_response, send_term_char='\r')

            time.sleep(0.01)

        logger.debug("Pump %s returned %r", self.name, ret)

        return ret

    def _get_move_status(self):
        ret = self.send_cmd("")

        if ret.endswith('>'):
            moving = True
            self._flow_dir = 1
        elif ret.endswith('<'):
            moving = True
            self._flow_dir = -1
        else:
            moving = False

        return moving

    def _set_flow_rate(self):
        self._flow_rate = self.round(self._flow_rate)
        self.send_cmd("RAT {} MM".format(self._flow_rate))

    def _set_refill_rate(self):
        self._refill_rate = self.round(self._refill_rate)
        self.send_cmd("RFR {} MM".format(self._refill_rate))

    def _get_delivered_volume(self):
        ret = self.send_cmd("DEL")

        vol = float(ret.split('\n')[1].strip())

        return vol

    def _send_dispense_cmd(self, vol):
        vol = self.round(vol)

        if vol > 0:
            self.send_cmd("DIR INF")
            self.send_cmd("CLD")
            self.send_cmd("TGT {}".format(vol))
            self.send_cmd("RUN")

    def _send_aspirate_cmd(self, vol):
        vol = self.round(vol)

        if vol > 0:
            self.send_cmd("DIR REF")
            self.send_cmd("CLD")
            self.send_cmd("TGT {}".format(vol))
            self.send_cmd("RUN")

    def _send_stop_cmd(self):
        self.send_cmd("STP")

    def _send_pump_cal_cmd(self):
        self.send_cmd("DIA {}".format(self.diameter))

    def round(self, val):
        oom = int('{:e}'.format(val).split('e')[1])

        if oom < 0:
            oom = 0

        num_dig = 6-(oom + 2)

        return round(val, num_dig)


class PicoPlusPump(SyringePump):
    """
    Harvard Pico Plus pump control.
    """

    def __init__(self, name, device, pump_address, diameter, max_volume, max_rate,
        syringe_id, dual_syringe, comm_lock=None):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """
        self._pump_address = int(pump_address)

        SyringePump.__init__(self, name, device, diameter, max_volume, max_rate,
            syringe_id, dual_syringe, comm_lock=comm_lock)

        logstr = ("Initializing Pico Plus pump {} on serial port {}".format(name, device))
        logger.info(logstr)

        self._units = 'mL/min'
        self._pump_base_units = 'mL/min'

        self._get_rates()

        now = datetime.datetime.now()
        self.send_cmd('time {}'.format(now.strftime('%m/%d/%y %H:%M:%S')))

    def connect(self):
        if not self.connected:
            with self.comm_lock:
                self.pump_comm = PicoPlusSerialComm(self.device, baudrate=115200)

            self.send_cmd('nvram none')

            self.connected = True

        return self.connected

    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.
        """

        logger.debug("Sending pump %s cmd %r", self.name, cmd)

        with self.comm_lock:

            ret = self.pump_comm.write("{:02d}{}".format(self._pump_address, cmd),
                self._pump_address, get_response=get_response)

            time.sleep(0.01)

        logger.debug("Pump %s returned %r", self.name, ret)

        return ret

    def _get_move_status(self):
        ret = self.send_cmd("")

        if ret.endswith('>'):
            moving = True
            self._flow_dir = 1
        elif ret.endswith('<'):
            moving = True
            self._flow_dir = -1
        else:
            moving = False

        return moving

    def _set_flow_rate(self):
        self.send_cmd("irate {} ml/min".format(self._flow_rate))

    def _set_refill_rate(self):
        self.send_cmd("wrate {} ml/min".format(self._refill_rate))

    def _get_delivered_volume(self):
        if self._flow_dir > 0:
            ret = self.send_cmd("ivolume")
            vol = ret.split('\r\n')[0].split(':')[1]
            vol, units = vol.split()
        else:
            ret = self.send_cmd("wvolume")
            vol = ret.split('\r\n')[0].split(':')[1]
            vol, units = vol.split()

        vol = float(vol)

        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

        return vol

    def _send_dispense_cmd(self, vol):
        self.send_cmd("cvolume")
        self.send_cmd("ctime")
        self.send_cmd("tvolume {} ml".format(vol))
        self.send_cmd("irun")

    def _send_aspirate_cmd(self, vol):
        # self.send_cmd("DIR REF")
        self.send_cmd("cvolume")
        self.send_cmd("ctime")
        self.send_cmd("tvolume {} ml".format(vol))
        self.send_cmd("wrun")

    def _send_stop_cmd(self):
        self.send_cmd("stop")

    def _send_pump_cal_cmd(self):
        self.send_cmd("diameter {}".format(self.diameter))
        self.send_cmd("svolume {} ml".format(self.max_volume))

    def _get_rates(self):
        fr = self.send_cmd('irate')
        rr = self.send_cmd('wrate')

        fr = fr.split('\r\n')[0].split(':')[1]
        fr, fr_units = fr.split()

        rr = rr.split('\r\n')[0].split(':')[1]
        rr, rr_units = rr.split()

        fr = float(fr)
        rr = float(rr)

        fr = self._convert_flow_rate(fr, fr_units, self._pump_base_units)
        rr = self._convert_flow_rate(rr, rr_units, self._pump_base_units)

        self._flow_rate = fr
        self._refill_rate = rr

    @property
    def force(self):
        ret = self.send_cmd('force')

        force = int(ret.split('\r\n')[0].split(':')[1].rstrip('%'))

        return force

    @force.setter
    def force(self, force):

        if force >= 1 and force <= 100:
            self.send_cmd('force {}'.format(force))
        else:
            logger.error(("Force must be between 1 and 100"))

    def round(self, val):
        oom = int('{:e}'.format(val).split('e')[1])

        if oom < 0:
            oom = 0

        num_dig = 6-(oom + 2)

        return round(val, num_dig)

class NE500Pump(SyringePump):
    """
    New Era Syringe Pump NE500 (OEM) control.
    """

    def __init__(self, name, device, pump_address, diameter, max_volume, max_rate,
        syringe_id, dual_syringe, comm_lock=None):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """
        self._pump_address = pump_address

        SyringePump.__init__(self, name, device, comm_lock=comm_lock)

        logstr = ("Initializing NE500 pump {} on serial port {}".format(name, device))
        logger.info(logstr)

        self._units = 'mL/min'
        self._pump_base_units = 'mL/min'

    def connect(self):
        if not self.connected:
            with self.comm_lock:
                self.pump_comm = SerialComm(self.device, baudrate=19200)

            self.connected = True

        return self.connected

    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.
        """

        logger.debug("Sending pump %s cmd %r", self.name, cmd)

        with self.comm_lock:
            ret = self.pump_comm.write("{}{}".format(self._pump_address, cmd),
                get_response=get_response, send_term_char='\r', term_char='\x03')

        if get_response:
            ret = ret.lstrip('\x02').rstrip('\x03').lstrip(self._pump_address)

            status = ret[0]
            ret = ret[1:]

            logger.debug("Pump %s returned %r", self.name, ret)
        else:
            ret = None
            status = None

        return ret, status

    def _get_move_status(self):
        ret, status = self.send_cmd("")

        if status == 'I':
            moving = True
            self._flow_dir = 1
        elif status == 'W':
            moving = True
            self._flow_dir = -1
        elif status == 'X' or status == 'T':
            moving = True
        else:
            moving = False

        return moving

    def _set_flow_rate(self):
        self._flow_rate = self.round(self._flow_rate)

        self.send_cmd("RAT{}".format(self._flow_rate))

    def _set_refill_rate(self):
        self._refill_rate = self.round(self._refill_rate)

        self.send_cmd("RAT{}".format(self._refill_rate))

    def _get_delivered_volume(self):
        ret, status = self.send_cmd("DIS")

        if self._flow_dir > 0:
            vol = ret.split('W')[0].lstrip('I').rstrip('W')
        else:
            vol = ret.split('W')[1].lstrip('I').rstrip('W')[:-2]

        vol = float(vol)

        return vol

    def _send_dispense_cmd(self, vol):
        vol = self.round(vol)

        self.send_cmd("DIRINF")
        self.send_cmd("CLDINF")
        self.send_cmd("RAT{}MM".format(self._flow_rate))
        self.send_cmd("VOL{}".format(vol))
        self.send_cmd("RUN")

    def _send_aspirate_cmd(self, vol):
        vol = self.round(vol)

        self.send_cmd("DIRWDR")
        self.send_cmd("CLDWDR")
        self.send_cmd("RAT{}MM".format(self._refill_rate))
        self.send_cmd("VOL{}".format(vol))
        self.send_cmd("RUN")

    def _send_stop_cmd(self):
        self.send_cmd("STP")

    def _send_pump_cal_cmd(self):
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


class HamiltonPSD6Pump(SyringePump):
    """
    Hamilton PSD6 pump (OEM) control.
    """

    def __init__(self, name, device, pump_address, diameter, max_volume,
        max_rate, syringe_id, dual_syringe, flow_rate_scale=1,
        flow_rate_offset=0, scale_type='both', comm_lock=None):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """
        self._pump_address = pump_address

        self._error_codes = {
            '0' :   'No error',
            '1' :   'Initialization error',
            '2' :   'Invalid command',
            '3' :   'Invalid operand',
            '4' :   'Invalid command sequence',
            '6' :   'EEPROM failure',
            '7' :   'Syringe not initialized',
            '9' :   'Syringe excessive backpressure (overload)',
            '10':   'Valve excessive backpressure (overload)',
            '11':   'Syringe move not allowed (wrong valve position)',
            '15':   'Pump busy',
            }

        self._error_translation = {
            '@' :   '0',
            "`" :   '0',
            'A' :   '1',
            'a' :   '1',
            'B' :   '2',
            'b' :   '2',
            'C' :   '3',
            'c' :   '3',
            'D' :   '4',
            'd' :   '4',
            'F' :   '6',
            'f' :   '6',
            'G' :   '7',
            'g' :   '7',
            'I' :   '9',
            'i' :   '9',
            'J' :   '10',
            'j' :   '10',
            'K' :   '11',
            'k' :   '11',
            'O' :   '15',
            'o' :   '15',
        }

        self._valve_status = {
            '0' : 'Not in defined position',
            '1' : 'Input',
            '2' : 'Output',
            '3' : 'Wash',
            '4' : 'Return',
            '5' : 'Bypass',
            '6' : 'Extra',
            }

        SyringePump.__init__(self, name, device, diameter, max_volume,
            max_rate, syringe_id, dual_syringe,
            flow_rate_scale=flow_rate_scale,
            flow_rate_offset=flow_rate_offset, scale_type=scale_type,
            comm_lock=comm_lock)

        logstr = ("Initializing Hamilton PSD6 pump {} on serial port {}".format(
            name, device))
        logger.info(logstr)

        self._units = 'mL/min'
        self._pump_base_units = 'mL/s'

        self.initialize()

    def connect(self):
        if not self.connected:
            with self.comm_lock:
                self.pump_comm = SerialComm(self.device, baudrate=9600)

            self.connected = True

        return self.connected

    def initialize(self):
        # self.send_cmd('Z10')
        self.send_cmd('h30001')

        #Check for syringe initialization and initialize if necessary
        ret, _ = self.send_cmd('?10000')
        if int(ret)%2 == 1:
            self.send_cmd('h10000')
            while self.is_moving():
                time.sleep(0.1)

        #Check for valve initialization and initialize if necessary
        ret, _ = self.send_cmd('?20000')
        if int(ret)%2 == 1:
            self.send_cmd('h20000')
            while self.is_moving():
                time.sleep(0.1)

        #Check if pump is in high resolution mode or not
        ret, _ = self.send_cmd('?11000')
        if int(ret)%2 == 0:
            self._high_res = False
            self._full_steps = 6000
        else:
            self._high_res = True
            self._full_steps = 48000

        #Get current flow and refill rate
        ret, _ = self.send_cmd('?2')

        rate = self._convert_steps_to_volume(int(ret))
        #Weird factor of 2 here, rates seem to be set in half steps
        rate = rate/2.
        self._flow_rate = self.round(rate)
        self._refill_rate = self.round(rate)

        #Get current start velocity in steps
        ret, _ = self.send_cmd('?1')
        self._default_start_velocity = int(ret)

        #Get current stop velocity in steps
        ret, _ = self.send_cmd('?3')
        self._default_stop_velocity = int(ret)

        #Get current acceleration in steps
        ret, _ = self.send_cmd('?10002')
        self._default_acceleration = int(ret)

        #Get current volume
        self.volume

        #Set Aux output 1 to 0
        self.set_trigger(False)


    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.
        """

        logger.debug("Sending pump %s cmd %r", self.name, cmd)

        with self.comm_lock:
            ret = self.pump_comm.write("/{}{}R".format(self._pump_address, cmd),
                get_response=get_response, send_term_char='\r',
                term_char='\x03\r\n')

        if get_response:
            # print('%r' % ret)
            ret = ret.lstrip('/').rstrip('\x03\r\n').lstrip(self._pump_address)

            status = ret[1]
            if len(ret)>2:
                ret = ret[2:]
            else:
                ret = None

            err_trans = self._error_translation[status]
            if err_trans != '0':
                logger.error("Pump %s %s", self.name,
                    self._error_codes[err_trans])

            logger.debug("Pump %s returned %r", self.name, ret)
        else:
            ret = None
            status = None

        # # Monitor for command completion
        # while True:
        #     ret = self.pump_comm.write("/{}QR".format(self._pump_address),
        #         get_response=get_response, send_term_char='\r',
        #         term_char='\x03\r\n')

        #     ret = ret.lstrip('/').rstrip('\x03\r\n').lstrip(self._pump_address)

        #     status = ret[1]

        #     if status == '`'
        return ret, status

    def is_moving(self):
        moving = self._get_move_status()
        self._is_flowing = moving
        return moving

    @property
    def volume(self):
        volume = self._get_volume()
        self._volume = volume

        volume = self._convert_volume(volume, self._pump_base_units.split('/')[0],
            self.units.split('/')[0])

        return volume

    @volume.setter
    def volume(self, volume):
        volume = self._get_volume()

        volume = self._convert_volume(volume, self.units.split('/')[0],
            self._pump_base_units.split('/')[0])

        self._volume = volume

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

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        if self.is_moving():
            logger.error('Cannot set pump %s flow rate while pump is moving.',
                self.name)

        else:
            logger.info("Setting pump %s infuse flow rate to %f %s", self.name, rate, self.units)

            rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

            self._flow_rate = self.round(rate)

            self._set_flow_rate()

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

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @refill_rate.setter
    def refill_rate(self, rate):
        if self.is_moving():
            logger.error('Cannot set pump %s refill rate while pump is moving.',
                self.name)
        else:
            logger.info("Setting pump %s refill flow rate to %f %s",
                self.name, rate, self.units)

            rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

            self._refill_rate = self.round(rate)
            # logger.info('Checking volume')

            self._set_refill_rate()

    def stop(self):
        """Stops all pump flow."""
        logger.info("Pump %s stopping all motions", self.name)
        self._send_stop_cmd()

        self.volume

        self._is_flowing = False
        self._flow_dir = 0

    def _get_move_status(self):
        ret, status = self.send_cmd("Q")

        if status == '`':
            moving = False
        elif status == '@':
            moving = True
        else:
            moving = False

        return moving

    def _convert_volume_to_steps(self, vol):
        steps = int(round(vol*(self._full_steps/self._max_volume)))

        return steps

    def _convert_steps_to_volume(self, steps):
        vol = steps*(self._max_volume/self._full_steps)

        return vol

    def _set_flow_rate(self):
        self._flow_rate = self.round(self._flow_rate)
        self._inner_set_velocity(self._flow_rate)

    def _set_refill_rate(self):
        self._refill_rate = self.round(self._refill_rate)
        self._inner_set_velocity(self._refill_rate)

    def _inner_set_velocity(self, rate):
        step_rate = self._calc_flow_rate(self._flow_rate)
        self.send_cmd("V{}".format(step_rate))

    def _calc_flow_rate(self, rate):
        step_rate = self._convert_volume_to_steps(rate)

        # For reasons not clear, there seems to be a factor of 2
        # between what you'd expect based on motor steps and what the
        # actual speed is
        step_rate = step_rate*2

        if step_rate < 2:
            step_rate = 2
        if step_rate > 10000:
            step_rate = 10000

        return step_rate

    def _get_volume(self):
        ret, status = self.send_cmd("?4")
        vol = self._convert_steps_to_volume(float(ret))

        return vol

    def _send_dispense_cmd(self, vol):
        cur_vol = self._convert_volume(self.volume, self.units.split('/')[0],
            self._pump_base_units.split('/')[0])
        new_vol = cur_vol - vol

        new_pos = self._convert_volume_to_steps(new_vol)

        step_rate = self._calc_flow_rate(self._flow_rate)

        self.send_cmd('V{}A{}'.format(step_rate, new_pos))

    def _send_aspirate_cmd(self, vol):
        cur_vol = self._convert_volume(self.volume, self.units.split('/')[0],
            self._pump_base_units.split('/')[0])
        new_vol = cur_vol + vol

        new_pos = self._convert_volume_to_steps(new_vol)

        step_rate = self._calc_flow_rate(self._refill_rate)

        self.send_cmd('V{}A{}'.format(step_rate, new_pos))

    def _send_stop_cmd(self):
        self.send_cmd("t")

    def _send_pump_cal_cmd(self):
        pass

    def get_valve_position(self):
        ret, status = self.send_cmd('?23000')
        status = self._valve_status[ret]

        return status

    def set_valve_position(self, pos):
        logger.info('Pump %s setting valve position %s', self.name, pos)
        if pos == 'Input':
            self.send_cmd('h23001')
        elif pos == 'Output':
            self.send_cmd('h23002')
        elif pos == 'Bypass':
            self.send_cmd('h23005')

    def set_trigger(self, trigger):
        if trigger:
            self.send_cmd('J1')
        else:
            self.send_cmd('J0')

    def dispense_with_trigger(self, vol, delay, units):
        orig_vol = copy.copy(vol)
        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

        if self._is_flowing:
            logger.debug("Stopping pump %s current motion before infusing", self.name)
            self.stop()

        cont = True

        if self.volume - vol < 0:
            logger.error(("Attempting to infuse {} mL, which is more than the "
                "current volume of the syringe ({} mL)".format(vol, self.volume)))
            cont = False

        vol = self.round(vol)

        if vol <= 0:
            logger.error(("Infuse volume must be positive."))
            cont = False

        if cont:

            logger.info("Pump %s infusing %f %s at %f %s", self.name, orig_vol, units,
                self.flow_rate, self.units)

            cur_vol = self._convert_volume(self.volume, self.units.split('/')[0],
                self._pump_base_units.split('/')[0])
            new_vol = cur_vol - vol

            new_pos = self._convert_volume_to_steps(new_vol)

            step_rate = self._calc_flow_rate(self._flow_rate)

            delay *= 1000 #convert to ms
            delay = int(delay)

            if delay > 5:
                delay_cmd = ''
                while delay-5 > 30000:
                    delay_cmd += 'M30000'
                    delay -= 30000

                if delay - 5 > 0:
                    delay_cmd += 'M{}'.format(delay-5)

                cmd = 'V{}J1M5J0{}A{}'.format(step_rate, delay_cmd, new_pos)
            else:
                cmd = 'V{}J1M5J0A{}'.format(step_rate, new_pos)


            self._flow_dir = 1

        self.send_cmd(cmd)


class SSINextGenPump(Pump):
    """
    Teledyne SSI Next Gen Pump communication control (e.g. Reaxus LD pumps).
    """

    def __init__(self, name, device, comm_lock=None, flow_rate_scale=1,
        flow_rate_offset=0, scale_type='both'):
        """
        This makes the initial serial connection, and then sets the MForce
        controller parameters to the correct values.

        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """

        self._accel_stop = threading.Event()
        self._accel_change = threading.Event()
        self._flow_rate_lock = threading.Lock()

        Pump.__init__(self, name, device, flow_rate_scale=flow_rate_scale,
            flow_rate_offset=flow_rate_offset, scale_type=scale_type,
            comm_lock=comm_lock)

        logstr = ("Initializing pump {} on serial port {}".format(self.name,
            self.device))
        logger.info(logstr)

        self.timeout=1 #Timeout to wait for response in s

        # self.keypad_enable(False)

        # All internal variables are stored in mL/min and psi, regardless of user/pump units
        self._units = 'mL/min'
        self._pump_base_units = 'mL/min'
        self._pressure_units = 'psi'
        self._pump_pressure_units = 'psi'
        self._flow_rate_val = 0 #Current set flow rate
        self._pump_max_pressure = -1 #Hardware pressure limit
        self._max_pressure = 10000 #Upper pressure limit
        self._min_pressure = 0 #Lower pressure limit
        self._is_flowing = False
        self._max_flow_rate = 10
        self._min_flow_rate = 0
        self.motor_stall_fault = False
        self.upl_fault = False
        self.lpl_fault = False
        self.leak_fault = False
        self.fault = False
        self._flow_rate_decimals = 3
        self._flow_rate_acceleration = 0.1 #Set to 0 for instant (as fast as the pump can) flow rate change
        self._ramping_flow = False
        self._stop_flow_after_ramp = False

        #Make sure parameters are set right
        ret = self.send_cmd('MF') #Max flow rate for the pump
        if ret.startswith('OK') and ret.endswith('/'):
            val = ret.split(':')[-1].strip('/')
            self._pump_max_flow_rate = float(val)
            self._max_flow_rate = self._pump_max_flow_rate
            self._flow_rate_decimals = len(val.split('.')[-1])
        else:
            self._pump_max_flow_rate = -1

        ret = self.send_cmd('PU') #Pressure unit for the pump
        if ret.startswith('OK') and ret.endswith('/'):
            self._pump_pressure_units = ret.split(',')[-1].strip('/')

        ret = self.send_cmd('MP') #Max pressure for the pump
        if ret.startswith('OK') and ret.endswith('/'):
            val = float(ret.split(':')[-1].strip('/'))

            self._pump_max_pressure = self._convert_pressure(val,
                self._pump_pressure_units, self._pressure_units)

            self._max_pressure = self._pump_max_pressure

        ret = self.send_cmd('LP')
        if ret.startswith('OK') and ret.endswith('/'):
            val = float(ret.split(':')[-1].strip('/'))

            self._min_pressure = self._convert_pressure(val,
                self._pump_pressure_units, self._pressure_units)

        ret = self.send_cmd('UP')
        if ret.startswith('OK') and ret.endswith('/'):
            val = float(ret.split(':')[-1].strip('/'))

            self._max_pressure = self._convert_pressure(val,
                self._pump_pressure_units, self._pressure_units)


        ret = self.send_cmd('LM1') #Detected leak does not cause fault

        self.get_status()
        self.get_faults()

    def connect(self):
        if not self.connected:
            with self.comm_lock:
                self.pump_comm = SerialComm(self.device)

            self.connected = True

        return self.connected

    @property
    def pressure_units(self):
        """
        Sets and returns the pump flow rate units. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :type: str
        """
        return self._pressure_units

    @pressure_units.setter
    def pressure_units(self, units):
        old_units = self._pressure_units

        if units.lower() in ['psi', 'bar', 'mpa', 'mbar']:
            self._pressure_units = units

            logger.info("Changed pump %s pressure units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change pump %s pressure units, units supplied were invalid: %s", self.name, units)

    @property
    def flow_rate(self, update=True):
        if update:
            self.get_status()

        rate = self._flow_rate

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s flow rate to %f %s", self.name, rate, self.units)

        #Convert rate to ml/min for pump
        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

        #Maximum continuous flow rate is 25 mL/min
        if rate>self.max_flow_rate:
            rate = self.max_flow_rate
            logger.warning("Requested flow rate > %f %s, setting pump %s flow rate to %f %s",
                self.max_flow_rate, self.units, self.name, self.max_flow_rate, self.units)

        if rate < self._min_flow_rate:
            rate = self._min_flow_rate
            logger.warning("Requested flow rate < %f %s, setting pump %s flow rate to %f %s",
                self._min_flow_rate, self.units, self.name, self._min_flow_rate, self.units)

        if round(rate, self._flow_rate_decimals) == 0 and rate != 0:
            logger.warning("Requested flow rate is smaller than the precision of the pump, "
                "so flow rate will be set to zero.")

        if not self.is_moving():
            self._send_flow_rate_cmd(rate)

        else:
            if self._ramping_flow:
                self._accel_stop.set()
            while self._ramping_flow:
                time.sleep(0.001)

            self.get_status()
            current_flow = copy.copy(self._flow_rate)

            if self._is_flowing:
                ramp_thread = threading.Thread(target=self._ramp_flow, args=(current_flow, rate))
                ramp_thread.start()
            else:
                self._send_flow_rate_cmd(rate)

    @property
    def _flow_rate(self):
        return self._flow_rate_val

    @_flow_rate.setter
    def _flow_rate(self, rate):
        with self._flow_rate_lock:
            self._flow_rate_val = rate

    @property
    def max_flow_rate(self):
        rate = self._max_flow_rate

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @max_flow_rate.setter
    def max_flow_rate(self, rate):
        logger.info("Setting pump %s max flow rate to %f %s", self.name, rate, self.units)

        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)


        if self._pump_max_flow_rate != -1 and rate > self._pump_max_flow_rate:

            logger.warning('Requested max flow rate %f is greater than the pump maximum '
                'flow rate %f. Setting the maximum flow rate the pump maximum', rate,
                self._pump_max_flow_rate)

            rate = self._pump_max_flow_rate

        self._max_flow_rate = rate

        if self._flow_rate > rate:
            logger.warning('Requested max flow rate %f is less than the current '
                'flow rate %f. Setting the flow rate to the new maximum',
                self._max_flow_rate, self._flow_rate)

            self.flow_rate = self.flow_rate

    @property
    def flow_rate_acceleration(self):
        rate = self._flow_rate_acceleration

        rate = self._convert_flow_accel(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate_acceleration.setter
    def flow_rate_acceleration(self, rate):
        logger.info("Setting pump %s flow rate acceleration to %f %s", self.name, rate, self.units)

        rate = self._convert_flow_accel(rate, self.units, self._pump_base_units)

        self._flow_rate_acceleration = rate

        if self._ramping_flow:
            self._accel_change.set()
            while self._accel_change.is_set():
                time.sleep(0.001)

    @property
    def max_pressure(self):
        pressure = self._max_pressure

        pressure = self._convert_pressure(pressure, self._pump_pressure_units,
            self.pressure_units)

        return pressure

    @max_pressure.setter
    def max_pressure(self, input_pressure):
        logger.info("Setting pump %s max pressure to %f %s", self.name, input_pressure, self.pressure_units)

        pressure = self._convert_pressure(input_pressure, self.pressure_units,
            self._pump_pressure_units)

        if self._pump_max_pressure != -1 and pressure > self._pump_max_pressure:
            logger.warning('Requested max pressure %f is greater than the pump maximum '
                'pressure %f. Setting the maximum pressure to the pump maximum', pressure,
                self._pump_max_pressure)
            pressure = self._pump_max_pressure

        self._max_pressure = pressure

        #There's weirdness in how you send the pressure command based on units
        if self._pump_pressure_units.lower() == 'bar':
            pressure = pressure*10
        elif self._pump_pressure_units.lower() == 'mpa':
            pressure = pressure*100

        self.send_cmd('UP{:0>5}'.format(int(round(pressure+0.00000001))))

    @property
    def min_pressure(self):
        pressure = self._min_pressure

        pressure = self._convert_pressure(pressure, self._pump_pressure_units,
            self.pressure_units)

        return pressure

    @min_pressure.setter
    def min_pressure(self, input_pressure):
        logger.info("Setting pump %s min pressure to %f %s", self.name, input_pressure, self.pressure_units)

        pressure = self._convert_pressure(input_pressure, self.pressure_units,
            self._pump_pressure_units)

        self._min_pressure = max(0, pressure)

        #There's weirdness in how you send the pressure command based on units
        if self._pump_pressure_units.lower() == 'bar':
            pressure = pressure*10
        elif self._pump_pressure_units.lower() == 'mpa':
            pressure = pressure*100

        self.send_cmd('LP{:0>5}'.format(int(round(pressure+0.00000001))))

    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.
        :type cmd: str, bytes

        :param get_response: Whether the program should get a response from the pump
        :type get_response: bool
        """
        logger.debug("Sending pump %s cmd %r", self.name, cmd)

        with self.comm_lock:
            ret = self.pump_comm.write(cmd, get_response, '\r', '/')

        if get_response:
            logger.debug("Pump %s returned %r", self.name, ret)

        return ret

    def is_moving(self):
        ret = self.send_cmd('CS')

        if ret.startswith('OK') and ret.endswith('/'):
            vals = ret.split(',')

            if vals[6] == '0':
                self._is_flowing = False
                self._flow_dir = 0
            else:
                self._is_flowing = True

        return self._is_flowing

    def start_flow(self, wait=True):
        logger.info("Pump %s starting continuous flow at %f %s", self.name,
            self.flow_rate, self.units)

        if not self.is_moving():
            logger.debug('pump is not moving')
            target_flow_rate = copy.copy(self.flow_rate)
            target_flow_rate = self._convert_flow_rate(target_flow_rate, self.units,
                self._pump_base_units)
            self.flow_rate = 0
            self.send_cmd("RU")

            start = time.time()
            if wait:
                while not self.is_moving():
                    time.sleep(0.01)

                    if time.time() - start > self.timeout:
                        break
                        logger.error('TImed out waiting for pump %s to start', self.name)

            self._flow_dir = 1
            ramp_thread = threading.Thread(target=self._ramp_flow, args=(self.flow_rate,
                target_flow_rate))
            ramp_thread.start()

        else:
            logger.debug('pump is moving')
            self.send_cmd("RU")

            start = time.time()
            if wait:
                while not self.is_moving():
                    time.sleep(0.01)

                    if time.time() - start > self.timeout:
                        break
                        logger.error('TImed out waiting for pump %s to start', self.name)

                    self._flow_dir = 1

    def start_immediate(self, wait=True):
        # Starts with no ramp! Really should only be used for testing!
        logger.info("Pump %s starting continuous flow at %f %s", self.name,
            self.flow_rate, self.units)

        self.send_cmd("RU")

        start = time.time()
        if wait:
            while not self._is_flowing:
                self.get_status()

                if time.time() - start > self.timeout:
                    break
                    logger.error('Timed out waiting for pump %s to start', self.name)

                self._flow_dir = 1

    def stop(self, wait=True):
        logger.info("Pump %s stopping all motions", self.name)

        if self.is_moving():
            if self._ramping_flow:
                self._accel_stop.set()
            while self._ramping_flow:
                time.sleep(0.01)

            self._stop_flow_after_ramp = True
            self.flow_rate = 0
            self._flow_dir = 0

        else:
            self.send_cmd("ST")

            start = time.time()
            if wait:
                while self._is_flowing:
                    self.get_status()

                    if time.time() - start > self.timeout:
                        break
                        logger.error('Timed out waiting for pump %s to stop', self.name)

                    self.flow_rate = 0
                    self._flow_dir = 0

    def abort(self):
        self.send_cmd("ST")

        if self._ramping_flow:
            self._accel_stop.set()
        while self._ramping_flow:
            time.sleep(0.01)

        self._stop_flow_after_ramp = True
        self.flow_rate = 0
        self._flow_dir = 0

        self.get_status()

    def is_ramping(self):
        return self._ramping_flow

    def _ramp_flow(self, current_flow_rate, target_flow_rate):
        # Input flow rates should be in pump base units, e.g. ml/min for SSI pumps
        logger.info('Ramping flow for pump %s from %f to %f at %f ml/min',
            self.name, current_flow_rate, target_flow_rate, self._flow_rate_acceleration)
        self._ramping_flow = True

        while self._accel_stop.is_set():
            time.sleep(0.001)

        if target_flow_rate > current_flow_rate:
            mult = 1
        else:
            mult = -1

        starting_flow_rate = copy.copy(current_flow_rate)

        start_time = time.time()
        prev_time = time.time()

        if self._flow_rate_acceleration == 0:
            self._send_flow_rate_cmd(target_flow_rate)
            current_flow_rate = target_flow_rate

        else:
            while target_flow_rate != current_flow_rate:
                if self._accel_stop.is_set():
                    break

                if self._accel_change.is_set():
                    starting_flow_rate = current_flow_rate
                    start_time = time.time()
                    self._accel_change.clear()

                current_time = time.time()

                time_since_start = current_time - start_time
                time_since_prev = current_time - prev_time

                expected_flow_rate = starting_flow_rate + mult*self._flow_rate_acceleration*time_since_start/60
                flow_rate_inc = mult*self._flow_rate_acceleration*time_since_prev/60

                if abs(expected_flow_rate - current_flow_rate) > abs(flow_rate_inc*1.1):
                    next_flow_rate = current_flow_rate + flow_rate_inc*1.1
                else:
                    next_flow_rate = expected_flow_rate

                if mult > 0 and next_flow_rate > target_flow_rate:
                    next_flow_rate = target_flow_rate
                elif mult < 0 and next_flow_rate < target_flow_rate:
                    next_flow_rate = target_flow_rate

                self._send_flow_rate_cmd(next_flow_rate)

                current_flow_rate = next_flow_rate
                time.sleep(0.01) #Yield for other threads

        self._ramping_flow = False
        self._accel_stop.clear()
        logger.info('Finished ramping flow for pump %s', self.name)

        if self._stop_flow_after_ramp and current_flow_rate <= 0:
            logger.info('Stopping pump %s after ramp', self.name)
            self.send_cmd("ST")

        else:
            self._flow_rate = current_flow_rate

        self._stop_flow_after_ramp = False

    def _send_flow_rate_cmd(self, rate):
        logger.debug('sending flow rate command')
        self._flow_rate = round(rate, self._flow_rate_decimals)

        scaled_rate = rate*self.flow_rate_scale+self.flow_rate_offset

        if scaled_rate < 0:
            scaled_rate = rate

        elif rate == 0:
            scaled_rate = 0

        if self.scale_type == 'up':
            if scaled_rate > rate:
                rate = scaled_rate
        elif self.scale_type == 'down':
            if scaled_rate < rate:
                rate = scaled_rate
        else:
            rate = scaled_rate

        rate = round(rate, self._flow_rate_decimals)

        if '.' in str(rate):
            rate_dec = '{:0<{fill}}'.format(str(rate).split('.')[-1], fill=self._flow_rate_decimals)
        else:
            rate_dec = ''.zfill(self._flow_rate_decimals)
        rate_str = '{:0>5}'.format('{}{}'.format(str(rate).split('.')[0], rate_dec))

        self.send_cmd('FI{}'.format(rate_str))

    def dispense(self, vol, units='mL'):
        vol = self._convert_volume(vol, units, self.units.split('/')[0])

        self._dispensing_volume = vol

        self.start_flow()

        dispense_thread = threading.Thread(target=self._run_dispense)
        dispense_thread.start()

    def _run_dispense(self):
        previous_time = time.time()
        previous_fr = self._flow_rate

        update_time = previous_time

        while self._is_flowing:
            current_fr = copy.copy(self._flow_rate)
            current_time = time.time()
            delta_vol = ((current_fr + previous_fr)/2./60.)*(current_time-previous_time)

            self._dispensing_volume -= delta_vol

            if self._flow_rate_acceleration > 0:
                stop_vol = (current_fr/self._flow_rate_acceleration)*(current_fr/2.)
            else:
                stop_vol = 0

            previous_time = current_time
            previous_fr = current_fr

            if current_time - update_time > 60:
                logger.info('Pump %s remaining dispense volume is %s mL',
                    self.name, self._dispensing_volume)
                update_time = current_time

            if self._dispensing_volume - stop_vol <= 0:
                self.stop()
                break

            time.sleep(0.1)


        logger.info('Finished dispense for pump %s', self.name)

    def get_status(self):
        ret = self.send_cmd('CS')

        if ret.startswith('OK') and ret.endswith('/'):
            vals = ret.split(',')

            rate = float(vals[1])
            scaled_rate = round((rate-self.flow_rate_offset)/self.flow_rate_scale,
                self._flow_rate_decimals)

            if self.scale_type == 'up':
                if scaled_rate < rate:
                    rate = scaled_rate
            elif self.scale_type == 'down':
                if scaled_rate > rate:
                    rate = scaled_rate
            else:
                rate = scaled_rate

            if rate <= 0:
                rate = 0

            self._flow_rate = rate
            self._max_pressure = float(vals[2])

            self._min_pressure = float(vals[3])

            self._pump_pressure_units = vals[4]

            if vals[6] == '0':
                self._is_flowing = False
                self._flow_dir = 0
            else:
                self._is_flowing = True

    def get_faults(self):
        ret = self.send_cmd('RF')

        # print(ret)

        if ret.startswith('OK') and ret.endswith('/'):
            vals = ret.rstrip('/').split(',')

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

        # print(ret)

        if ret.startswith('OK') and ret.endswith('/'):
            val = ret.split(':')[-1].strip('/')

            if val == '0':
                self.leak_fault = False
            else:
                self.leak_fault = True

        self.fault = self.motor_stall_fault or self.upl_fault or self.lpl_fault or self.leak_fault

        faults = {'Fault' : self.fault, 'Motor stall': self.motor_stall_fault,
            'Upper pressure limit' : self.upl_fault,
            'Lower pressure limit' : self.lpl_fault, 'Leak': self.leak_fault,
            }

        return faults

    def get_pressure(self):
        ret = self.send_cmd('PR')

        if ret.startswith('OK') and ret.endswith('/'):
            val = float(ret.split(',')[-1].strip('/'))

            pressure = self._convert_pressure(val, self._pump_pressure_units,
                self.pressure_units)

        else:
            pressure = -1

        pressure = round(pressure, 4)

        return pressure

    def clear_faults(self):
        self.send_cmd('#', False)
        self.send_cmd('CF')
        self.get_faults()

    def keypad_enable(self, enable):
        if enable:
            self.send_cmd('KE')
        else:
            self.send_cmd('KD')

    def disconnect(self):
        logger.debug("Closing pump %s serial connection", self.name)
        self.keypad_enable(True)

        self._is_dispensing = False
        self._accel_stop.set()

class OB1(object):
    def __init__(self, name, device, comm_lock=None, calib_path=None):

        self.name = name
        self.device = device

        if comm_lock is None:
            self.comm_lock = threading.RLock()
        else:
            self.comm_lock = comm_lock

        self.connected = False

        self.connect()

        self.calib = None

        if calib_path is not None:
            self.load_calibration(calib_path)

        else:
            calib = (ctypes.c_double*1000)()
            error = Elveflow.Elveflow_Calibration_Default(ctypes.byref(calib),
                1000)

            self.calib = calib

            self._check_error(error)

        self.remote = False

        self._connected_channels = []

    def connect(self):
        if not self.connected:
            self.instr_ID = ctypes.c_int32()

            with self.comm_lock:
                error = Elveflow.OB1_Initialization(self.device.encode('ascii'),
                    0, 0, 0, 0, ctypes.byref(self.instr_ID))

                self._check_error(error)

            self.connected = True

        return self.connected

    def calibrate(self):
        calib = (ctypes.c_double*1000)()
        error = Elveflow.OB1_Calib(self.instr_ID.value, calib, 1000)

        self._check_error(error)

        self.calib = calib

    def save_calibration(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        error = Elveflow.Elveflow_Calibration_Save(path.encode('ascii'),
            ctypes.byref(self.calib), 1000)

        self._check_error(error)

    def load_calibration(self, path):
        path = os.path.abspath(os.path.expanduser(path))
        calib = (ctypes.c_double*1000)()

        error = Elveflow.Elveflow_Calibration_Load(path.encode('ascii'),
            ctypes.byref(calib), 1000)

        self.calib = calib
        self._check_error(error)

    def _check_error(self, error):
        error = int(error)

        if error in utils.elveflow_errors:
            logger.error('%s Error: %s', self.name, utils.elveflow_errors[error])
        elif error != 0:
            logger.error('%s Error: LabView Error Code %s', self.name, error)

    def disconnect(self):
        if self.connected:
            with self.comm_lock:
                for channel in self._connected_channels:
                    channel.connected = False

                error = Elveflow.OB1_Destructor(self.instr_ID.value)

                self._check_error(error)

                self.connected = False

    def stop(self):
        pass


class OB1Pump(Pump):
    def __init__(self, name, device, channel, min_pressure, max_pressure,
        ob1_device, P=0, I=0, D=0, pid_sample_time=0.1, bfs_instr_ID=None,
        comm_lock=None, fm_comm_lock=None, flow_rate_scale=1, flow_rate_offset=0,
        scale_type='both'):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str

        Note: comm_lock needs to be an RLock for this device
        """

        logstr = ("Initializing pump {} on serial port {}".format(name,
            device))
        logger.info(logstr)

        self._ob1 = ob1_device

        Pump.__init__(self, name, device, flow_rate_scale=flow_rate_scale,
            flow_rate_offset=flow_rate_offset, scale_type=scale_type,
            comm_lock=comm_lock)

        self._pump_base_units = 'uL/min'
        self._units = self._pump_base_units
        self._pump_pressure_units = 'mbar'
        self._pressure_units = 'mbar'
        self._target_pressure = 0
        self._target_flow = 0
        self._min_pressure = min_pressure
        self._max_pressure = max_pressure

        self._channel = int(channel)
        self._ob1_chan = ctypes.c_int32(self._channel)

        self._has_sensor = False #Used for reading flow rate but not necessarily PID
        self._has_flow_meter = False #Used for PID
        self._PID_mode = False
        self._P = P
        self._I = I
        self._D = D
        self._pid_sample_time = pid_sample_time

        self._PID = pid.PID(self._P, self._I, self._D, 0)
        self._PID.sample_time = self._pid_sample_time
        self._PID.output_limits = (self._min_pressure, self._max_pressure)

        self._pid_on_evt = threading.Event()
        self._abort_pid_evt = threading.Event()

        self._bfs_instr_ID = bfs_instr_ID
        self._fm_comm_lock = fm_comm_lock

        if self._bfs_instr_ID is not None:
            self.pid_thread = threading.Thread(target=self.run_PID)
            self.pid_thread.daemon = True
            self.pid_thread.start()

            self._PID_mode = True
            self._has_flow_meter = True

            if self._fm_comm_lock is None:
                self._fm_comm_lock = threading.Lock()


    def connect(self):
        if not self.connected:

            if not self._ob1.connected:
                self._ob1.connect()

            self.instr_ID = self._ob1.instr_ID
            self.calib = self._ob1.calib

            self.connected = True

        return self.connected

    @property
    def pressure_units(self):
        """
        Sets and returns the pump flow rate units. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :type: str
        """
        return self._pressure_units

    @pressure_units.setter
    def pressure_units(self, units):
        old_units = self._pressure_units

        if units.lower() in ['psi', 'bar', 'mpa', 'mbar']:
            self._pressure_units = units

            logger.info("Changed pump %s pressure units from %s to %s", self.name, old_units, units)
        else:
            logger.warning("Failed to change pump %s pressure units, units supplied were invalid: %s", self.name, units)

    @property
    def flow_rate(self):
        """
        Sets and returns the pump flow rate in units specified by ``Pump.units``.
        Can be set while the pump is moving, and it will update the flow rate
        appropriately.

        :type: float
        """
        with self.comm_lock:
            if self._has_sensor and self._ob1.remote:
                pressure, rate = self._read_remote_channel()

            elif self._has_sensor and not self._ob1.remote:
                data_sens=ctypes.c_double()
                error = Elveflow.OB1_Get_Sens_Data(self.instr_ID.value,
                    self._ob1_chan, 1, ctypes.byref(data_sens))

                self._check_error(error)
                rate = float(data_sens)

            else:
                rate = self._flow_rate

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

        with self.comm_lock:
            if self._has_flow_meter:
                self._PID.setpoint = rate
                self._is_flowing = True

                if not self._PID_mode:
                    pressure = self._inner_get_pressure()
                    self._PID.set_auto_mode(True, pressure)
                    self._PID_mode = True

                if not self._pid_on_evt.is_set():
                    self._pid_on_evt.set()

            else:
                logger.error('Failed to set flow rate for %s because there '
                    'is not a flow meter associated with the device.', self.name)

        if self._is_flowing:
            self._flow_rate = rate

            if self._flow_rate > 0:
                self._flow_dir = 1
            else:
                self._flow_dir = -1

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

        Note: for the OB1 there's no clear distinction between setting the
        target flow rate and starting flow, so this does nothing.
        """
        pass

    def dispense(self, vol, units='uL'):
        """
        Dispenses a fixed volume.

        :param vol: Volume to dispense
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

        self._dispensing_volume = abs(vol)

        dispense_thread = threading.Thread(target=self._run_dispense)
        dispense_thread.start()

    def aspirate(self, vol, units='uL'):
        """
        Aspirates a fixed volume.

        :param vol: Volume to aspirate
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

        self._dispensing_volume = abs(vol)

        dispense_thread = threading.Thread(target=self._run_dispense)
        dispense_thread.start()

    def _run_dispense(self):
        previous_time = time.time()
        previous_fr = self.flow_rate

        update_time = previous_time

        while self._is_flowing:
            current_fr = copy.copy(self.flow_rate)
            current_time = time.time()
            delta_vol = ((current_fr + previous_fr)/2./60.)*(current_time-previous_time)

            self._dispensing_volume -= abs(delta_vol)

            previous_time = current_time
            previous_fr = current_fr

            if current_time - update_time > 60:
                logger.info('Pump %s remaining dispense/aspirate volume is %s uL',
                    self.name, self._dispensing_volume)
                update_time = current_time

            if self._dispensing_volume <= 0:
                self.stop()
                break

            time.sleep(0.1)


        logger.info('Finished dispense/aspirate for pump %s', self.name)

    def set_pressure(self, pressure):
        self._target_pressure = pressure

        pressure = self._convert_pressure(pressure, self.pressure_units,
            self._pump_pressure_units)
        if self._PID_mode:
            self._pid_on_evt.clear()
            self._PID.auto_mode = False
            self._PID_mode = False
            time.sleep(self._pid_sample_time)

        self._inner_set_pressure(pressure)


    def _inner_set_pressure(self, pressure):
        if pressure > self._max_pressure:
            logger.warning('Pressure %s is greater than %s max pressure,'
                'setting pressure to max', pressure, self.name)
            pressure = self._max_pressure

        if pressure < self._min_pressure:
            logger.warning('Pressure %s is less than %s min pressure,'
                'setting pressure to min', pressure, self.name)
            pressure = self._min_pressure

        with self.comm_lock:

            if not self._ob1.remote:
                set_pressure = float(pressure) #mbarr
                set_pressure = ctypes.c_double(set_pressure)#convert to c_double

                error = Elveflow.OB1_Set_Press(self.instr_ID.value, self._ob1_chan,
                    set_pressure, ctypes.byref(self.calib), 1000)

                self._check_error(error)
            else:
                if self._has_flow_meter and self._PID_mode:
                    self._start_remote_PID(False)

                self._set_remote_target(pressure)

    def get_pressure(self):
        pressure = self._inner_get_pressure()

        pressure = self._convert_pressure(pressure, self._pump_pressure_units,
            self.pressure_units)

        return pressure

    def _inner_get_pressure(self):
        with self.comm_lock:
            if not self._ob1.remote:
                get_pressure = ctypes.c_double()

                error = Elveflow.OB1_Get_Press(self.instr_ID.value, self._ob1_chan,
                    1, ctypes.byref(self.calib), ctypes.byref(get_pressure), 1000)

                self._check_error(error)

                pressure = float(get_pressure.value)

            else:
                pressure, flow = self._read_remote_channel()

        return pressure

    def run_PID(self):
        prev_dens = 0
        while True:
            if self._abort_pid_evt.is_set():
                break

            if self._pid_on_evt.is_set():
                start_t = time.time()

                fr, dens, temp = self.get_fm_values()

                delta_t = time.time() - start_t

                if dens > 700 and (prev_dens/dens < 1.05 and prev_dens/dens > 0.95):
                    pressure = self._PID(fr)
                    self._inner_set_pressure(pressure)

                prev_dens = dens

                while time.time() - start_t < self._pid_sample_time - delta_t:
                    time.sleep(0.01)

            else:
                time.sleep(0.1)

    def get_fm_values(self):
        with self._fm_comm_lock:
            density = ctypes.c_double(-1)
            error = Elveflow.BFS_Get_Density(self._bfs_instr_ID.value, ctypes.byref(density))
            density = float(density.value)
            self._check_error(error)

            temperature = ctypes.c_double(-1)
            error = Elveflow.BFS_Get_Temperature(self._bfs_instr_ID.value, ctypes.byref(temperature))
            temperature = float(temperature.value)
            self._check_error(error)

            flow = ctypes.c_double(-1)
            error = Elveflow.BFS_Get_Flow(self._bfs_instr_ID.value, ctypes.byref(flow))
            flow = float(flow.value)
            self._check_error(error)

        return flow, density, temperature

    def set_PID_values(self, P, I, D):
        self._P = P
        self._I = I
        self._D = D

        self._PID.tunings = (P, I, D)
    """
    The code below is for use with the Remote PID control built into the
    Elveflow API. I found that didn't work well, but leaving it here in case
    I want to retest it at some point with a new version of the API.

    @property
    def flow_rate(self):
        with self.comm_lock:
            if self._has_sensor and self._ob1.remote:
                pressure, rate = self._read_remote_channel()

            elif self._has_sensor and not self._ob1.remote:
                data_sens=ctypes.c_double()
                error = Elveflow.OB1_Get_Sens_Data(self.instr_ID.value,
                    self._ob1_chan, 1, ctypes.byref(data_sens))

                self._check_error(error)
                rate = float(data_sens)

            else:
                rate = self._flow_rate

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

        with self.comm_lock:
            if self._has_flow_meter and self._PID_mode and self._ob1.remote:
                self._set_remote_target(rate)
                self._is_flowing = True

            elif self._has_flow_meter and self._ob1.remote:
                self._start_remote_PID(True)
                self._set_remote_target(rate)
                self._is_flowing = True

            elif self._has_flow_meter:
                self._start_remote()
                self._start_remote_PID(True)
                self._set_remote_target(rate)
                self._is_flowing = True
            else:
                logger.error('Failed to set flow rate for %s because there '
                    'is not a flow meter associated with the device.', self.name)

        if self._is_flowing:
            self._flow_rate = rate

            if self._flow_rate > 0:
                self._flow_dir = 1
            else:
                self._flow_dir = -1

    def _start_remote(self):
        with self.comm_lock:
            error = Elveflow.OB1_Start_Remote_Measurement(self.instr_ID.value,
                ctypes.byref(self.calib), 1000)
            self._check_error(error)

            self._ob1.remote = True

    def _stop_remote(self):
        with self.comm_lock:
            error = Elveflow.OB1_Stop_Remote_Measurement(self.instr_ID.value)
            self._check_error(error)

            self._ob1.remote = False

    def _start_remote_PID(self, start_running):
        if start_running:
            running = ctypes.c_int32(1)
        else:
            running = ctypes.c_int32(0)

        with self.comm_lock:
            error = Elveflow.PID_Set_Running_Remote(self.instr_ID.value, self._ob1_chan,
                running)
            self._check_error(error)

        self._PID_mode = start_running

    def _read_remote_channel(self):
        data_sens=ctypes.c_double()
        data_reg=ctypes.c_double()

        with self.comm_lock:
            error=Elveflow.OB1_Get_Remote_Data(self.instr_ID.value,
                self._ob1_chan, ctypes.byref(data_reg), ctypes.byref(data_sens))

        pressure = float(data_reg.value)
        flow = float(data_sens.value)

        return pressure, flow

    def _set_remote_target(self, val):
        set_target = float(val)
        set_target = ctypes.c_double(set_target)#convert to c_double

        with self.comm_lock:
            error = Elveflow.OB1_Set_Remote_Target(self.instr_ID.value,
                self._ob1_chan, set_target)

            self._check_error(error)

    def initialize_remote_PID(self, P, I, D, bfs_instr_ID, start_running):
        # Flow meter must already be in remote mode
        # D not used at the moment for OB1
        self._P = P
        self._I = I

        P = float(P)
        P = ctypes.c_double(P)
        I = float(I)
        I = ctypes.c_double(I)
        sensor_channel = ctypes.c_int32(1) # Shouldn't matter

        if start_running:
            running = ctypes.c_int32(1)
        else:
            running = ctypes.c_int32(0)

        with self.comm_lock:
            error = Elveflow.PID_Add_Remote(self.instr_ID.value, self._ob1_chan,
                bfs_instr_ID.value, sensor_channel, P, I, running)
            self._check_error(error)

        self._has_flow_meter = True
        self._PID_mode = start_running


    def set_PID_values(self, P, I, D, reset_err=True):
        #D not used at the moment for OB1
        self._P = P
        self._I = I

        P = float(P)
        P = ctypes.c_double(P)
        I = float(I)
        I = ctypes.c_double(I)

        if reset_err:
            reset = ctypes.c_int32(1)
        else:
            reset = ctypes.c_int32(0)

        with self.comm_lock:
            error = Elveflow.PID_Set_Params_Remote(self.instr_ID.value,
                self._ob1_chan, reset, P, I)
            self._check_error(error)
    """

    def stop(self):
        """Stops all pump flow."""
        if self._has_flow_meter:
            self.flow_rate = 0

        else:
            self.set_pressure(0)

        self._is_flowing = False
        self._is_dispensing = False

    def _check_error(self, error):
        error = int(error)

        if error in utils.elveflow_errors:
            logger.error('%s Error: %s', self.name, utils.elveflow_errors[error])
        elif error != 0:
            logger.error('%s Error: LabView Error Code %s', self.name, error)

    def disconnect(self):
        if self.connected:
            if self._ob1.connected:
                self._ob1.disconnect()

            self.connected = False

class KPHM100Pump(M50Pump):
    """
    .. todo:: This class doesn't know when the pump is done dispensing. This leads
        to unncessary stop signals being sent to the pump, and makes the log harder
        to follow. This could be fixed, when I have time.

    This class provides control for the Kamoer Flud Tech KPHM100 peristaltic pump
    with stepper motor, using the MForce controller.
    """

    def __init__(self, name, device, comm_lock=None, flow_cal=319.2):
        """
        This makes the initial serial connection, and then sets the MForce
        controller parameters to the correct values.

        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str

        :param flow_cal: The pump-specific flow calibration, in uL/rev. Defaults to 628 uL/rev
        :type flow_cal: float
        """
        # Based on the M50, since it uses the same controller and is the same
        # type of pump (e.g. continuous flow vs. syringe)
        M50Pump.__init__(self, name, device, comm_lock=comm_lock,
            flow_cal=flow_cal, backlash_cal=0)

        self.send_cmd('RC 33') #Run current to 33%, equal to 1.0 A.

        self._gear_ratio = 1 #No gearing

        self.cal = 200*256/self._flow_cal #Calibration value in (micro)steps/uL
            #full steps/rev * microsteps/full step / uL/revolution = microsteps/uL

    @property
    def flow_rate(self):
        rate = float(self._flow_rate)/self.cal

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s flow rate to %f %s", self.name, rate, self.units)

        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

        #Maximum continuous flow rate is 210 mL/min
        if rate>210000/60.:
            rate = 210000/60.
            logger.warning("Requested flow rate > 210 mL/min, setting pump %s flow rate to 210 mL/min", self.name)
        elif rate<-210000/60.:
            rate = -210000/60.
            logger.warning("Requested flow rate > 210 mL/min, setting pump %s flow rate to -210 mL/min", self.name)

        # #Minimum flow rate is 1 uL/min
        # if abs(rate) < 1/60. and rate != 0:
        #     if rate>0:
        #         logger.warning("Requested flow rate < 1 uL/min, setting pump %s flow rate to 1 uL/min", self.name)
        #         rate = 1/60.
        #     else:
        #         logger.warning("Requested flow rate < 1 uL/min, setting pump %s flow rate to -1 uL/min", self.name)
        #         rate = -1/60.


        self._flow_rate = int(round(rate*self.cal))

        if self._is_flowing and not self._is_dispensing:
            self.send_cmd("SL {}".format(self._flow_rate))
        else:
            self.send_cmd("VM {}".format(abs(self._flow_rate)))

class LongerL1001S2Pump(Pump):
    """
    Darwin microfluidics has some very useful documentation. I found it much
    more helpful than the pump manual:
    https://blog.darwin-microfluidics.com/how-to-control-the-longer-l100-1s-2-pump-via-python/
    https://blog.darwin-microfluidics.com/control-command-string-generator-for-longer-peristaltic-pumps/

    Note that other longer peristatltic pumps have different command sets.
    """

    def __init__(self, name, device, pump_addr, comm_lock=None, flow_cal=1):
        """
        This makes the initial serial connection, and then sets the MForce
        controller parameters to the correct values.

        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str

        :param flow_cal: The pump-specific flow calibration, in uL/rev.
        :type flow_cal: float

        """
        Pump.__init__(self, name, device, comm_lock=comm_lock)

        logstr = ("Initializing pump {} on serial port {}".format(self.name,
            self.device))
        logger.info(logstr)

        self._pump_addr = int(pump_addr)

        #Make sure parameters are set right

        self._units = 'mL/min'
        self._pump_base_units = 'mL/min'

        self._flow_cal = float(flow_cal)
        self.cal = 1/self._flow_cal

        if self.connected:
            self.is_moving()

    def connect(self):
        if not self.connected:
            with self.comm_lock:
                self.pump_comm = LongerSerialComm(self.device, baudrate=9600)

            self.connected = True

        return self.connected

    @property
    def flow_rate(self):
        rate = self._flow_rate/self.cal

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s flow rate to %f %s", self.name, rate, self.units)

        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

        #Maximum continuous flow rate is 100 rpm
        if abs(rate)*self.cal > 100:

            if rate > 0:
                rate = 100./self.cal
            else:
                rate = -100./self.cal
            logger.warning("Requested flow rate > 100 rpm, setting pump %s flow rate to 100 rpm", self.name)

        #Minimum flow rate is 0.01 rpm
        if abs(rate)*self.cal < 0.01 and rate != 0:
            if rate>0:
                rate = 0.01/self.cal
            else:
                rate = -0.01/self.cal

            logger.warning("Requested flow rate < 0.01 rpm, setting pump %s flow rate to 0.01 rpm", self.name)

        stop_pump = not self.is_moving()

        if rate > 0:
            self._flow_dir = 1
            pump_dir = 'CW'
        else:
            self._flow_dir = -1
            pump_dir = 'CCW'

        self._flow_rate = round(rate*self.cal, 2)

        cmd = self.generate_cmd_string('set', stop_pump, pump_dir, self._flow_rate)

        self.send_cmd(cmd)

    def generate_cmd_string(self, cmd, stop_pump=True, pump_dir='CW', pump_speed=1):
        """
        pump_speed is in RPM
        """
        pump_speed = abs(pump_speed)
        cmd_hdr = bytearray(b'\xE9')

        cmd_addr = '{:02}'.format(self._pump_addr)

        if cmd == 'set':
            cmd_len = '06'
            cmd_start = '57 4A' #WJ in hex

            if pump_speed >= 10:
                #Resolution of 0.1 for speeds >= 10 rpm
                speed = int(round(pump_speed,1)*100) #Speed in units of 0.01 RPM
            else:
                #Resolution of 0.01 for speeds < 10 rpm
                speed = int(round(pump_speed,2)*100) #Speed in units of 0.01 RPM

            hex_speed = hex(speed)

            speed_cmd = '{:0>4}'.format(hex_speed[2:]).upper()
            speed_cmd = '{} {}'.format(speed_cmd[:2], speed_cmd[2:])

            if stop_pump:
                stop_cmd = '00'
            else:
                stop_cmd = '01'

            if pump_dir == 'CW':
                #clockwise
                dir_cmd = '00'
            else:
                #counter clockwise
                dir_cmd = '01'

            cmd = '{} {} {} {}'.format(cmd_start, speed_cmd, stop_cmd, dir_cmd)

        elif cmd == 'status':
            cmd_len = '02'
            cmd = '52 4A' #RJ in hex

        #calculate checksum
        pump_binary = int(cmd_addr.replace(' ', ''), 16)
        len_binary = int(cmd_len.replace(' ', ''), 16)

        cmd_binary = [int(c, 16) for c in cmd.split(' ')]

        check_binary = pump_binary ^ len_binary

        for c in cmd_binary:
            check_binary ^= c

        cmd_check = '{:02}'.format(hex(check_binary)[2:]).upper()

        # make final command
        full_cmd =  '{} {} {} {}'.format(cmd_addr, cmd_len, cmd, cmd_check)

        binary_cmd = cmd_hdr + bytearray.fromhex(full_cmd)

        return binary_cmd

    def send_cmd(self, cmd, get_response=True):
        """
        Sends a command to the pump.

        :param cmd: The command to send to the pump.
        :type cmd: str, bytes

        :param get_response: Whether the program should get a response from the pump
        :type get_response: bool
        """
        logger.debug("Sending pump %s cmd %r", self.name, cmd)

        with self.comm_lock:
            ret = self.pump_comm.write(cmd, get_response)

        if get_response:
            logger.debug("Pump %s returned %r", self.name, ret)

        return ret


    def is_moving(self):
        cmd = self.generate_cmd_string('status')

        status = self.send_cmd(cmd)

        status = status.split(' ')

        cmd = bytes.fromhex(''.join(status[3:5])).decode('ascii')

        if cmd == 'RJ':
            speed = round(int(''.join(status[5:7]), 16)/100., 2)
            running = int(status[7], 16)
            direction = int(status[8], 16)

            if direction == 1:
                self._flow_dir = -1
            else:
                self._flow_dir = 1

            self._flow_rate = speed

            if running == 1:
                status = True
            else:
                status = False
                self._is_dispensing = False

            self._is_flowing = status

            logger.debug("Pump %s moving: %s", self.name, str(self._is_flowing))

            return self._is_flowing

        else:
            return False

    def start_flow(self):
        if self._is_flowing:
            logger.debug("Stopping pump %s current motion before starting continuous flow", self.name)
            self.stop()

        logger.info("Pump %s starting continuous flow at %f %s", self.name, self.flow_rate, self.units)

        if self._flow_dir == 1:
            pump_dir = 'CW'
        else:
            pump_dir = 'CCW'

        cmd = self.generate_cmd_string('set', False, pump_dir, self._flow_rate)

        self.send_cmd(cmd)

        self._is_flowing = True

        if self._flow_rate > 0:
            self._flow_dir = 1
        elif self._flow_rate < 0:
            self._flow_dir = -1

    def dispense(self, vol, units='uL'):
        """
        Dispenses a fixed volume.

        :param vol: Volume to dispense
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        vol = self._convert_volume(vol, units, self.units.split('/')[0])

        self._dispensing_volume = abs(vol)

        self.start_flow()

        dispense_thread = threading.Thread(target=self._run_dispense)
        dispense_thread.start()

    def aspirate(self, vol, units='uL'):
        """
        Aspirates a fixed volume.

        :param vol: Volume to aspirate
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        self.dispense(vol, units)

    def _run_dispense(self):
        previous_time = time.time()
        previous_fr = self.flow_rate

        update_time = previous_time

        while self._is_flowing:
            current_fr = copy.copy(self.flow_rate)
            current_time = time.time()
            delta_vol = ((current_fr + previous_fr)/2./60.)*(current_time-previous_time)

            self._dispensing_volume -= abs(delta_vol)

            previous_time = current_time
            previous_fr = current_fr

            if current_time - update_time > 60:
                logger.info('Pump %s remaining dispense/aspirate volume is %s uL',
                    self.name, self._dispensing_volume)
                update_time = current_time

            if self._dispensing_volume <= 0:
                self.stop()
                break

            time.sleep(0.1)


        logger.info('Finished dispense/aspirate for pump %s', self.name)

    def stop(self):
        logger.info("Pump %s stopping all motions", self.name)

        if self._flow_dir == 1:
            pump_dir = 'CW'
        else:
            pump_dir = 'CCW'

        cmd = self.generate_cmd_string('set', True, pump_dir, self._flow_rate)

        self.send_cmd(cmd)

        self._is_flowing = False
        self._is_dispensing = False

class SoftPump(Pump):
    """
    This class contains the settings and communication for a generic pump.
    It is intended to be subclassed by other pump classes, which contain
    specific information for communicating with a given pump. A pump object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`PumpCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, name, device, comm_lock=None):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """
        Pump.__init__(self, name, device, comm_lock=comm_lock)

        self._is_aspirating = False

        self._units = 'mL/min'
        self._pump_base_units = 'mL/s'

        self._dispensing_volume = 0
        self._aspirating_volume = 0

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
        rate = self._flow_rate

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)
        self._flow_rate = rate

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
        self._flow_dir = 1

    def dispense(self, vol, units='uL'):
        """
        Dispenses a fixed volume.

        :param vol: Volume to dispense
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

        self._dispensing_volume = vol
        self._is_dispensing = True
        self._is_flowing = True
        self._is_aspirating = False
        self._flow_dir = 1

        pass #Should be implimented in each subclass

    def aspirate(self, vol, units='uL'):
        """
        Aspirates a fixed volume.

        :param vol: Volume to aspirate
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

        self._aspirating_volume = vol
        self._is_aspirating = True
        self._is_flowing = True
        self._is_dispensing = False
        self._flow_dir = -1

        pass #Should be implimented in each subclass

    def _sim_flow(self):
        previous_time = time.time()

        while self.connected:
            flow_rate = self._flow_rate

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
        self._flow_dir = 0

    def disconnect(self):
        """Close any communication connections"""
        self.connected = False
        self.sim_thread.join()

class SoftSyringePump(SyringePump):
    """
    A software syringe pump for testing.
    """

    def __init__(self, name, device, diameter, max_volume, max_rate, syringe_id,
        dual_syringe=False, comm_lock=None):
        """
        :param device: The device comport as sent to pyserial
        :type device: str

        :param name: A unique identifier for the pump
        :type name: str
        """

        SyringePump.__init__(self, name, device, diameter, max_volume, max_rate,
            syringe_id, dual_syringe=False, comm_lock=comm_lock)

        self._is_aspirating = False

        self._units = 'mL/min'
        self._pump_base_units = 'mL/s'

        self._dispensing_volume = 0
        self._aspirating_volume = 0

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
        rate = self._flow_rate

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @flow_rate.setter
    def flow_rate(self, rate):
        logger.info("Setting pump %s infuse flow rate to %f %s", self.name, rate, self.units)

        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

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

        rate = self._convert_flow_rate(rate, self._pump_base_units, self.units)

        return rate

    @refill_rate.setter
    def refill_rate(self, rate):
        logger.info("Setting pump %s refill flow rate to %f %s", self.name, rate, self.units)

        rate = self._convert_flow_rate(rate, self.units, self._pump_base_units)

        self._refill_rate = rate

    @property
    def volume(self):
        volume = self._volume
        volume = self._convert_volume(volume, self._pump_base_units.split('/')[0],
            self.units.split('/')[0])

        return volume

    @volume.setter
    def volume(self, volume):
        volume = self._convert_volume(volume, self.units.split('/')[0],
            self._pump_base_units.split('/')[0])
        self._volume = volume

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
        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

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
            self._is_aspirating = False
            self._flow_dir = 1

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
        vol = self._convert_volume(vol, units, self._pump_base_units.split('/')[0])

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
            self._is_dispensing = False
            self._flow_dir = -1

    def _sim_flow(self):
        previous_time = time.time()

        while self.connected:
            if self._is_dispensing:
                flow_rate = self._flow_rate

                delta_vol = flow_rate*(time.time()-previous_time)
                previous_time = time.time()
                self._dispensing_volume = self._dispensing_volume - delta_vol

                if self._dispensing_volume <= 0:
                    self.stop()

                self._volume = self._volume - delta_vol

            elif self._is_aspirating:
                flow_rate = self._refill_rate

                delta_vol = flow_rate*(time.time()-previous_time)
                previous_time = time.time()
                self._aspirating_volume = self._aspirating_volume - delta_vol

                if self._aspirating_volume <= 0:
                    self.stop()

                self._volume = self._volume + delta_vol

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
        self.connected = False
        self.sim_thread.join()

known_pumps = {
    'VICI M50'      : M50Pump,
    'PHD 4400'      : PHD4400Pump,
    'Pico Plus'     : PicoPlusPump,
    'NE 500'        : NE500Pump,
    'Hamilton PSD6' : HamiltonPSD6Pump,
    'SSI Next Gen'  : SSINextGenPump,
    'OB1'           : OB1,
    'OB1 Pump'      : OB1Pump,
    'KPHM100'       : KPHM100Pump,
    'Longer L100S2' : LongerL1001S2Pump,
    'Soft'          : SoftPump,
    'Soft Syringe'  : SoftSyringePump,
    }

class PumpCommThread(utils.CommManager):
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

    def __init__(self, name):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_pumps``.

        :param collections.deque command_queue: The queue used to pass commands to
            the thread.

        :param threading.Event abort_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        utils.CommManager.__init__(self, name)

        logger.info("Starting pump control thread: %s", self.name)

        self._commands = {
            'connect'           : self._connect_device,
            'get_flow_rate'     : self._get_flow_rate,
            'set_flow_rate'     : self._set_flow_rate,
            'get_refill_rate'   : self._get_refill_rate,
            'set_refill_rate'   : self._set_refill_rate,
            'set_flow_accel'    : self._set_flow_acceleration,
            'set_units'         : self._set_units,
            'start_flow'        : self._start_flow,
            'stop'              : self._stop_flow,
            'aspirate'          : self._aspirate,
            'dispense'          : self._dispense,
            'is_moving'         : self._is_moving,
            'send_cmd'          : self._send_pump_cmd,
            'disconnect'        : self._disconnect_device,
            'get_volume'        : self._get_volume,
            'set_volume'        : self._set_volume,
            'dispense_all'      : self._dispense_all,
            'aspirate_all'      : self._aspirate_all,
            'set_pump_cal'      : self._set_pump_cal,
            'get_status'        : self._get_status,
            'get_status_multi'  : self._get_status_multiple,
            'set_pump_dual_syringe': self._set_dual_syringe,
            'get_max_pressure'  : self._get_max_pressure,
            'set_max_pressure'  : self._set_max_pressure,
            'get_min_pressure'  : self._get_min_pressure,
            'set_min_pressure'  : self._set_min_pressure,
            'get_pressure'      : self._get_pressure,
            'set_pressure'      : self._set_pressure,
            'get_pressure_units': self._get_pressure_units,
            'set_pressure_units': self._set_pressure_units,
            'get_faults'        : self._get_faults,
            'clear_faults'      : self._clear_faults,
            'get_force'         : self._get_force,
            'set_force'         : self._set_force,
            'get_settings'      : self._get_settings,
            'get_flow_dir'      : self._get_flow_dir,
            'get_pump'          : self._get_pump,
            'initialize_ob1_pid': self._initialize_ob1_pid,
            'set_pid'           : self._set_pid,
            'get_valve_pos'     : self._get_valve_pos,
            'set_valve_pos'     : self._set_valve_pos,
            'get_full_status'   : self._get_full_status,
            }

        self.known_devices = known_pumps

    def _additional_pre_connect_device(self, name, device_type, device, kwargs):
        if device_type == 'OB1 Pump':
            ob1_device_name = kwargs.pop('ob1_device_name')
            calib_path = kwargs.pop('calib_path')

            if ob1_device_name in self._connected_devices:
                ob1_device = self._connected_devices[ob1_device_name]
            else:
                ob1_device = None

            if ob1_device is None:
                ob1_kwargs = {'comm_lock': kwargs['comm_lock'],
                    'calib_path': calib_path}

                self._connect_device(ob1_device_name, 'OB1', device, **ob1_kwargs)

                ob1_device = self._connected_devices[ob1_device_name]

            kwargs['ob1_device'] = ob1_device

        return kwargs

    def _get_flow_rate(self, name, **kwargs):

        logger.debug("Getting pump %s flow rate", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.flow_rate

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Pump %s flow rate is %f", name, val)

    def _set_flow_rate(self, name, val, **kwargs):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float val: The flow rate for the pump.
        """
        logger.info("Setting pump %s flow rate to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.flow_rate = val

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Pump %s flow rate set", name)

    def _get_refill_rate(self, name, **kwargs):

        logger.debug("Getting pump %s flow rate", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.refill_rate

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Pump %s flow rate is %f", name, val)

    def _set_flow_acceleration(self, name, val, **kwargs):
        logger.info("Setting pump %s flow rate acceleration to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.flow_rate_acceleration = val

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Pump %s flow rate acceleration set", name)

    def _set_refill_rate(self, name, val, **kwargs):
        """
        This method sets the refill rate for a pump. Only works for pumps that
        have a refill rate, for example the Harvard syringe pumps.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float val: The refill rate for the pump.
        """
        logger.info("Setting pump %s refill rate to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.refill_rate = val

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Pump %s refill rate set", name)

    def _set_units(self, name, val, **kwargs):
        """
        This method sets the units for the flow rate for a pump. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param str val: The units for the pump.
        """
        logger.info("Setting pump %s units to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.units = val

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Pump %s units set", name)

    def _set_volume(self, name, val, **kwargs):
        """
        This method sets the volume for a fixed volume pump such as a syringe pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float volume: The volume for the pump.
        """
        logger.info("Setting pump %s volume to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.volume = val

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Pump %s volume set", name)

    def _get_volume(self, name, **kwargs):
        """
        This method gets the volume of a fixed volume pump such as a syringe pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.debug("Getting pump %s volume", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.volume

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Pump %s volume is %f", name, val)

    def _start_flow(self, name, **kwargs):
        """
        This method starts continuous flow for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.info("Starting pump %s continuous flow", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)
        callback = kwargs.pop('callback', None)

        device = self._connected_devices[name]
        device.start_flow(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        if callback is not None:
            callback()

        logger.debug("Pump %s flow started", name)

    def _stop_flow(self, name, **kwargs):
        """
        This method stops all flow (continuous or finite) for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.info("Stopping pump %s", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.stop(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Pump %s stopped", name)

    def _aspirate(self, name, val, **kwargs):
        """
        This method aspirates a fixed volume.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float val: The volume to aspirate.
        """
        if 'units' in kwargs:
            units = kwargs['units']
        else:
            units = ''
        logger.info("Aspirating %s %s from pump %s", val, units, name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)
        callback = kwargs.pop('callback', None)

        device = self._connected_devices[name]
        device.aspirate(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        if callback is not None:
            callback()

        logger.debug("Pump %s aspiration started", name)

    def _aspirate_all(self, name, **kwargs):
        """
        This method aspirates all remaning volume for a fixed volume pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.info("Aspirating all for pump %s", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)
        callback = kwargs.pop('callback', None)

        device = self._connected_devices[name]
        device.aspirate_all(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        if callback is not None:
            callback()

        logger.debug("Pump %s aspiration started", name)

    def _dispense(self, name, val, **kwargs):
        """
        This method dispenses a fixed volume.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float val: The volume to dispense.
        """
        if 'units' in kwargs:
            units = kwargs['units']
        else:
            units = ''
        logger.info("Dispensing %s %s from pump %s", val, units, name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)
        callback = kwargs.pop('callback', None)

        device = self._connected_devices[name]
        device.dispense(val, **kwargs)

        self._return_value((name, cmd, True), comm_name)

        if callback is not None:
            callback()

        logger.debug("Pump %s dispensing started", name)

    def _dispense_all(self, name, **kwargs):
        """
        This method dispenses all remaining volume for a fixed volume pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.info("Dispensing all from pump %s", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)
        callback = kwargs.pop('callback', None)

        device = self._connected_devices[name]
        device.dispense_all(**kwargs)

        self._return_value((name, cmd, True), comm_name)

        if callback is not None:
            callback()

        logger.debug("Pump %s dispensing started", name)

    def _is_moving(self, name, **kwargs):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :rtype: bool
        """
        logger.debug("Checking if pump %s is moving", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.is_moving()

        self._return_value((name, cmd, val), comm_name)
        logger.debug("Pump %s is moving: %s", name, str(val))

    def _set_pump_cal(self, name, diameter, max_volume, max_rate, syringe_id,
        **kwargs):
        logger.info("Setting pump %s calibration parameters", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_pump_cal(diameter, max_volume, max_rate, syringe_id, **kwargs)

        self._return_value((name, cmd, True), comm_name)
        logger.debug("Pump %s calibration parameters set", name)

    def _set_dual_syringe(self, name, dual_syringe, **kwargs):
        logger.info("Setting pump %s dual syringe to %s", name, str(dual_syringe))

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.dual_syringe = dual_syringe

        self._return_value((name, cmd, True), comm_name)
        logger.debug("Pump %s dual syringe parameter set", name)

    def _get_status(self, name, **kwargs):
        logger.debug("Getting pump status")

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        val = self._get_pump_remote_status(name)

        self._return_value((name, cmd, val), comm_name)

    def _get_faults(self, name, **kwargs):
        logger.debug('Getting pump faults')

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_faults(**kwargs)

        self._return_value((name, cmd, val), comm_name)

    def _clear_faults(self, name, **kwargs):
        logger.info('Clearing pump faults')

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.clear_faults(**kwargs)

        self._return_value((name, cmd, True), comm_name)

    def _get_status_multiple(self, names, **kwargs):
        logger.debug('Getting multiple pump status')

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        vals = []
        for name in names:
            val = self._get_pump_remote_status(name)

            vals.append(val)

        self._return_value((names, cmd, [names, vals]), comm_name)

    def _get_pump_remote_status(self, name):
        device = self._connected_devices[name]

        status = self._get_status_inner(device)

        try:
            faults = device.get_faults()
        except Exception:
            faults = {'Fault' : False}

        try:
            syringe_id = device.syringe_id
        except Exception:
            syringe_id = None

        try:
            is_dispensing = device.is_dispensing()
        except Exception:
            is_dispensing = None

        status['is_dispensing'] = is_dispensing
        status['faults'] = faults
        status['syringe_id'] = syringe_id

        return status

    def _get_max_pressure(self, name, **kwargs):
        logger.debug("Getting pump %s max pressure", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.max_pressure

        self._return_value((name, cmd, val), comm_name)
        logger.info("Pump %s max pressure is %s", name, val)

    def _set_max_pressure(self, name, val, **kwargs):
        logger.info("Setting pump %s max pressure to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.max_pressure = val

        self._return_value((name, cmd, True), comm_name)
        logger.debug("Pump %s max pressure set", name)

    def _get_min_pressure(self, name, **kwargs):
        logger.debug("Getting pump %s min pressure", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.min_pressure

        self._return_value((name, cmd, val), comm_name)
        logger.info("Pump %s min pressure is %s", name, val)

    def _set_min_pressure(self, name, val, **kwargs):
        logger.info("Setting pump %s min pressure to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.min_pressure = val

        self._return_value((name, cmd, True), comm_name)
        logger.debug("Pump %s min pressure set", name)

    def _get_force(self, name, **kwargs):
        logger.debug("Getting pump %s force", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.force

        self._return_value((name, cmd, val), comm_name)
        logger.debug("Pump %s force is %s", name, val)

    def _set_force(self, name, val, **kwargs):
        logger.info("Setting pump %s force to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.force = val

        self._return_value((name, cmd, True), comm_name)
        logger.debug("Pump %s force set", name)

    def _get_pressure_units(self, name, **kwargs):
        logger.debug("Getting pump %s pressure units", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.pressure_units

        self._return_value((name, cmd, val), comm_name)
        logger.debug("Pump %s pressure units is %s", name, val)

    def _set_pressure_units(self, name, val, **kwargs):
        logger.info("Setting pump %s pressure units to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.pressure_units = val

        self._return_value((name, cmd, True), comm_name)
        logger.debug("Pump %s pressure units set", name)

    def _get_pressure(self, name, **kwargs):
        logger.debug("Getting pump %s pressure", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_pressure()

        self._return_value((name, cmd, val), comm_name)
        logger.debug("Pump %s pressure is %s", name, val)

    def _set_pressure(self, name, val, **kwargs):
        logger.info("Setting pump %s pressure to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_pressure(val)

        self._return_value((name, cmd, True), comm_name)
        logger.debug("Pump %s pressure set", name)

    def _get_settings(self, name, **kwargs):
        logger.debug("Getting pump %s settings", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        try:
            max_pres = device.max_pressure
        except Exception:
            max_pres = None

        try:
            min_pres = device.min_pressure
        except Exception:
            min_pres = None

        try:
            pres_units = device.pressure_units
        except Exception:
            pres_units = None

        try:
            faults = device.get_faults()
        except Exception:
            faults = None

        try:
            syringe_id = device.syringe_id
        except Exception:
            syringe_id = None

        units = device.units

        settings = {
            'max_pressure'  : max_pres,
            'min_pressure'  : min_pres,
            'pressure_units': pres_units,
            'units'         : units,
            'faults'        : faults,
            'syringe_id'    : syringe_id,
        }

        self._return_value((name, cmd, settings), comm_name)
        logger.debug("Pump %s settings are %s", name, settings)

    def _get_flow_dir(self, name, **kwargs):
        logger.debug("Getting pump %s flow direction", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_flow_dir()

        self._return_value((name, cmd, val), comm_name)
        logger.debug("Pump %s flow direction is %s", name, val)

    def _get_pump(self, name, **kwargs):
        logger.debug("Getting pump %s", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        if name in self._connected_devices:
            device = self._connected_devices[name]
        else:
            device = None

        self._return_value((name, cmd, device), comm_name)
        logger.debug("Got pump %s", name)

    def _initialize_ob1_pid(self, name, P, I, D, fm_instr_id, start_running,
        **kwargs):
        logger.info("Initializing pump %s PID, P: %s, I: %s D: %s", name,
            P, I, D)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.initialize_remote_PID(P, I, D, fm_instr_id, start_running)

        self._return_value((name, cmd, True), comm_name)
        logger.debug("Initialized pump %s PID", name)

    def _set_pid(self, name, P, I, D, **kwargs):
        logger.info("Setting pump %s PID, P: %s, I: %s D: %s", name,
            P, I, D)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_PID_values(P, I, D)

        self._return_value((name, cmd, True), comm_name)
        logger.debug("Set pump %s PID", name)

    def _get_valve_pos(self, name, **kwargs):

        logger.debug("Getting pump %s valve position", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_valve_position()

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Pump %s valve position is %s", name, val)

    def _set_valve_pos(self, name, val, **kwargs):
        """
        This method sets the valve position for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float val: The valve position for the pump.
        """
        logger.info("Setting pump %s valve position to %s", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        device.set_valve_position(val)

        self._return_value((name, cmd, True), comm_name)

        logger.debug("Pump %s valve position set", name)

    def _get_full_status(self, name, **kwargs):
        logger.debug('Getting pump %s full status', name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]

        status = self._get_status_inner(device)

        self._return_value((name, cmd, status), comm_name)
        logger.debug('Got pump %s full status', name)

    def _get_status_inner(self, device):
        is_moving = device.is_moving()
        flow_rate = device.flow_rate
        flow_dir = device.get_flow_dir()
        pressure = device.get_pressure()
        if device.is_syringe_pump:
            volume = device.volume
            refill_rate = device.refill_rate
        else:
            volume = None
            refill_rate = None
        valve_pos = device.get_valve_position()

        status = {
            'is_moving'     : is_moving,
            'flow_rate'     : flow_rate,
            'flow_dir'      : flow_dir,
            'pressure'      : pressure,
            'volume'        : volume,
            'refill_rate'   : refill_rate,
            'valve_pos'     : valve_pos,
            }

        return status

    def _send_pump_cmd(self, name, val, get_response=True, **kwargs):
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
        logger.info("Sending pump %s cmd %r", name, val)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        ret = device.send_cmd(cmd, get_response, **kwargs)

        self._return_value((name, cmd, ret), comm_name)

        logger.debug("Pump %s command sent", name)

    def _additional_abort(self):
        for name, device in self._connected_devices.items():
            device.stop()


# List of syringe calibrations
known_syringes = {
    '30 mL, EXEL'           : {'diameter': 23.5, 'max_volume': 30,
                                'max_rate': 70},
    '1 mL, Medline P.C.'    : {'diameter': 4.69, 'max_volume': 1.0,
                                'max_rate': 5},
    '3 mL, Medline P.C.'    : {'diameter': 9.1, 'max_volume': 3.0,
                                'max_rate': 11},
    '6 mL, Medline P.C.'    : {'diameter': 12.8, 'max_volume': 6,
                                'max_rate': 23},
    '10 mL, Medline P.C.'   : {'diameter': 16.31, 'max_volume': 10,
                                'max_rate': 31},
    '20 mL, Medline P.C.'   : {'diameter': 19.84, 'max_volume': 20,
                                'max_rate': 55},
    '0.25 mL, Hamilton Glass': {'diameter': 2.30, 'max_volume': 0.25,
                                'max_rate': 11},
    '0.5 mL, Hamilton Glass': {'diameter': 3.26, 'max_volume': 0.5,
                                'max_rate': 11},
    '1.0 mL, Hamilton Glass': {'diameter': 4.61, 'max_volume': 1.0,
                                'max_rate': 11},
    '0.1 mL, Hamilton Glass': {'diameter': 1.46, 'max_volume': 0.1,
                                'max_rate': 1},
    '0.05 mL, Hamilton Glass': {'diameter': 1.03, 'max_volume': 0.05,
                                'max_rate': 1},
    }


class PumpPanel(utils.DevicePanel):
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
    def __init__(self, parent, panel_id, settings, *args, **kwargs):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_pumps``.

        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        :param str panel_name: Name for the panel
        """

        self.known_syringes = known_syringes

        super(PumpPanel, self).__init__(parent, panel_id, settings,
            *args, **kwargs)

        self._current_move_status = False
        self._current_flow_rate = -1
        self._current_refill_rate = -1
        self._current_flow_accel = -1
        self._current_volume = -1
        self._current_units = ''
        self._current_pressure = -1
        self._current_min_pressure = -1
        self._current_max_pressure = -1
        self._current_pressure_units = ''
        self._current_flow_dir = 1
        self._current_valve_position = ''

    def _create_layout(self):
        """Creates the layout for the panel."""

        device_data = self.settings['device_data']

        if 'flow_rate' in device_data['ctrl_args']:
            flow_rate = str(device_data['ctrl_args']['flow_rate'])
        else:
            flow_rate = '0.1'

        if 'refill_rate' in device_data['ctrl_args']:
            refill_rate = str(device_data['ctrl_args']['refill_rate'])
        else:
            refill_rate = '0.1'

        if 'flow_accel' in device_data['ctrl_args']:
            flow_accel = str(device_data['ctrl_args']['flow_accel'])
        else:
            flow_accel = '0.1'

        if 'units' in device_data['ctrl_args']:
            units = str(device_data['ctrl_args']['units'])
        else:
            units = 'mL/min'

        self.pump_type = device_data['args'][0]

        self.status = wx.StaticText(self, label='Not connected')
        self.syringe_volume = wx.StaticText(self, label='0', size=self._FromDIP((40,-1)),
            style=wx.ST_NO_AUTORESIZE)
        self.syringe_volume_label = wx.StaticText(self, label='Current volume:')
        self.syringe_volume_units = wx.StaticText(self, label='mL')
        self.set_syringe_volume = wx.Button(self, label='Set Current Volume')
        self.set_syringe_volume.Bind(wx.EVT_BUTTON, self._on_set_volume)
        self.syringe_vol_gauge = wx.Gauge(self, size=self._FromDIP((40, -1)),
            style=wx.GA_HORIZONTAL|wx.GA_SMOOTH)
        self.syringe_vol_gauge_low = wx.StaticText(self, label='0')
        self.syringe_vol_gauge_high = wx.StaticText(self, label='')
        self.pressure_label = wx.StaticText(self, label='Pressure:')
        self.pressure = wx.StaticText(self, label='0', size=self._FromDIP((40, -1)),
            style=wx.ST_NO_AUTORESIZE)
        self.pressure_units_lbl = wx.StaticText(self, label='psi')
        self.flow_readback_label = wx.StaticText(self, label='Flow Rate:')
        self.flow_readback = wx.StaticText(self, label='0', size=self._FromDIP((40, -1)),
            style=wx.ST_NO_AUTORESIZE)
        self.flow_readback_units = wx.StaticText(self, label='mL/min')

        self.vol_gauge = wx.BoxSizer(wx.HORIZONTAL)
        self.vol_gauge.Add(self.syringe_vol_gauge_low,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.vol_gauge.Add(self.syringe_vol_gauge, 1, border=self._FromDIP(2),
            flag=wx.LEFT|wx.EXPAND)
        self.vol_gauge.Add(self.syringe_vol_gauge_high, border=self._FromDIP(2),
            flag=wx.LEFT|wx.ALIGN_CENTER_VERTICAL)

        status_grid = wx.GridBagSizer(vgap=self._FromDIP(5), hgap=self._FromDIP(5))
        status_grid.Add(wx.StaticText(self, label='Pump name:'), (0,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(wx.StaticText(self, label=self.name), (0,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        status_grid.Add(wx.StaticText(self, label='Status: '), (1,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.status, (1,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        status_grid.Add(self.flow_readback_label, (2, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.flow_readback, (2, 1), flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.flow_readback_units, (2, 2), flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.syringe_volume_label, (3,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.syringe_volume, (3,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.syringe_volume_units, (3,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(self.vol_gauge, (4,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        status_grid.Add(self.set_syringe_volume, (5,1), span=(1,2),
            flag=wx.LEFT|wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL)

        status_grid.AddGrowableCol(1)
        status_grid.AddGrowableCol(2)



        self.ssi_status_sizer = wx.FlexGridSizer(cols=3, vgap=self._FromDIP(5),
            hgap=self._FromDIP(5))
        self.ssi_status_sizer.Add(self.pressure_label, flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_status_sizer.Add(self.pressure, flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_status_sizer.Add(self.pressure_units_lbl, flag=wx.ALIGN_CENTER_VERTICAL)


        self.status_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Info'),
            wx.VERTICAL)
        self.status_sizer.Add(status_grid, 1, wx.EXPAND)
        self.status_sizer.Add(self.ssi_status_sizer, flag=wx.EXPAND)

        self.mode_ctrl = wx.Choice(self, choices=['Continuous flow', 'Fixed volume'])
        self.mode_ctrl.SetSelection(0)
        self.direction_lbl = wx.StaticText(self, label='Direction:')
        self.direction_ctrl = wx.Choice(self, choices=['Dispense', 'Aspirate'])
        self.direction_ctrl.SetSelection(0)
        self.flow_rate_ctrl = wx.TextCtrl(self, value=flow_rate,
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))
        self.flow_units_lbl = wx.StaticText(self, label='mL/min')
        self.flow_accel_lbl = wx.StaticText(self, label='Flow accel.:')
        self.flow_accel_ctrl = wx.TextCtrl(self, value=flow_accel,
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))
        self.flow_accel_units_lbl = wx.StaticText(self, label='mL/min^2')
        self.refill_rate_lbl = wx.StaticText(self, label='Refill rate:')
        self.refill_rate_ctrl = wx.TextCtrl(self, value=refill_rate,
            size=self._FromDIP((60,-1)), validator=utils.CharValidator('float'))
        self.refill_units_lbl = wx.StaticText(self, label='mL/min')
        self.volume_lbl = wx.StaticText(self, label='Volume:')
        self.volume_ctrl = wx.TextCtrl(self, size=self._FromDIP((60,-1)),
            validator=utils.CharValidator('float'))
        self.vol_units_lbl = wx.StaticText(self, label='mL')

        #Only turn on for the SSI pump
        self.flow_accel_lbl.Hide()
        self.flow_accel_ctrl.Hide()
        self.flow_accel_units_lbl.Hide()


        self.mode_ctrl.Bind(wx.EVT_CHOICE, self._on_mode)

        basic_ctrl_sizer = wx.GridBagSizer(vgap=self._FromDIP(2),
            hgap=self._FromDIP(2))
        basic_ctrl_sizer.Add(wx.StaticText(self, label='Mode:'), (0,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.mode_ctrl, (0,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.direction_lbl, (1,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.direction_ctrl, (1,1), span=(1,2),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(wx.StaticText(self, label='Flow rate:'), (2,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_rate_ctrl, (2,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_units_lbl, (2,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(self.flow_accel_lbl, (3,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_accel_ctrl, (3,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.flow_accel_units_lbl, (3,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(self.refill_rate_lbl, (4,0),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.refill_rate_ctrl, (4,1),
            flag=wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.refill_units_lbl, (4,2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT)
        basic_ctrl_sizer.Add(self.volume_lbl, (5,0),
            flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.volume_ctrl, (5,1),
            flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN|wx.ALIGN_CENTER_VERTICAL)
        basic_ctrl_sizer.Add(self.vol_units_lbl, (5,2),
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

        self.pressure_ctrl = utils.ValueEntry(self._on_set_pressure, self,
            size=self._FromDIP((60, -1)), validator=utils.CharValidator('float_te'))
        self.ob1_ctrl_sizer = wx.BoxSizer()
        self.ob1_ctrl_sizer.Add(wx.StaticText(self, label='Pressure:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ob1_ctrl_sizer.Add(self.pressure_ctrl, border=self._FromDIP(2),
            flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT)


        self.valve_ctrl = wx.Choice(self, choices=['Input', 'Output', 'Bypass'])
        self.valve_ctrl.Bind(wx.EVT_CHOICE, self._on_valve_change)
        self.hamilton_ctrl_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.hamilton_ctrl_sizer.Add(wx.StaticText(self, label='Valve position:'))
        self.hamilton_ctrl_sizer.Add(self.valve_ctrl)

        self.vol_unit_ctrl = wx.Choice(self, choices=['nL', 'uL', 'mL'])
        self.vol_unit_ctrl.SetStringSelection(units.split('/')[0])
        self.time_unit_ctrl = wx.Choice(self, choices=['s', 'min'])
        self.time_unit_ctrl.SetStringSelection(units.split('/')[1])

        self.vol_unit_ctrl.Bind(wx.EVT_CHOICE, self._on_units)
        self.time_unit_ctrl.Bind(wx.EVT_CHOICE, self._on_units)

        gen_settings_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2),
            hgap=self._FromDIP(2))
        gen_settings_sizer.AddGrowableCol(1)
        gen_settings_sizer.Add(wx.StaticText(self, label='Volume unit:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(self.vol_unit_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(wx.StaticText(self, label='Time unit:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_settings_sizer.Add(self.time_unit_ctrl,
            flag=wx.ALIGN_CENTER_VERTICAL)


        syr_types = sorted(self.known_syringes.keys(), key=lambda x: float(x.split()[0]))
        self.syringe_type = wx.Choice(self, choices=syr_types)
        self.syringe_type.SetSelection(0)
        self.syringe_type.Bind(wx.EVT_CHOICE, self._on_syringe_type)
        self.dual_syringe = wx.Choice(self, choices=['True', 'False'])
        self.dual_syringe.SetStringSelection('False')

        self.phd4400_settings_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2),
            hgap=self._FromDIP(2))
        self.phd4400_settings_sizer.Add(wx.StaticText(self, label='Syringe type:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.phd4400_settings_sizer.Add(self.syringe_type,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.phd4400_settings_sizer.Add(wx.StaticText(self, label='Dual syr. joined:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.phd4400_settings_sizer.Add(self.dual_syringe,
            flag=wx.ALIGN_CENTER_VERTICAL)


        # self.force = utils.ValueEntry(self._on_force_change, self,
        #     size=self._FromDIP((60, -1)), validator=utils.CharValidator('int_te'))

        # self.picoplus_settings_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2),
        #     hgap=self._FromDIP(2))
        # self.picoplus_settings_sizer.Add(wx.StaticText(self, label='Force (%):'),
        #     flag=wx.ALIGN_CENTER_VERTICAL)
        # self.picoplus_settings_sizer.Add(self.force, flag=wx.ALIGN_CENTER_VERTICAL)


        self.max_pressure = utils.ValueEntry(self._on_max_pressure_change, self,
            size=self._FromDIP((60, -1)), validator=utils.CharValidator('float_te'))
        self.max_pressure_units_lbl = wx.StaticText(self, label='psi')
        self.min_pressure = utils.ValueEntry(self._on_min_pressure_change, self,
            size=self._FromDIP((60, -1)), validator=utils.CharValidator('float_te'))
        self.min_pressure_units_lbl = wx.StaticText(self, label='psi')
        self.pressure_units = wx.Choice(self, choices=['psi', 'MPa', 'bar'])
        self.pressure_units.SetSelection(1)
        self.pressure_units.Bind(wx.EVT_CHOICE, self._on_pressure_units)


        self.ssi_settings_sizer = wx.FlexGridSizer(cols=3, vgap=self._FromDIP(2),
            hgap=self._FromDIP(2))
        self.ssi_settings_sizer.Add(wx.StaticText(self, label='Max pressure:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_settings_sizer.Add(self.max_pressure,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_settings_sizer.Add(self.max_pressure_units_lbl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_settings_sizer.Add(wx.StaticText(self, label='Min. pressure:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_settings_sizer.Add(self.min_pressure,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_settings_sizer.Add(self.min_pressure_units_lbl,
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_settings_sizer.Add(wx.StaticText(self, label='Pressure units:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ssi_settings_sizer.Add(self.pressure_units,
            flag=wx.ALIGN_CENTER_VERTICAL)


        self.feedback_p = utils.ValueEntry(self._on_pid_change, self,
            size=self._FromDIP((60, -1)), validator=utils.CharValidator('float_te'))
        self.feedback_i = utils.ValueEntry(self._on_pid_change, self,
            size=self._FromDIP((60, -1)), validator=utils.CharValidator('float_te'))
        self.feedback_d = utils.ValueEntry(self._on_pid_change, self,
            size=self._FromDIP((60, -1)), validator=utils.CharValidator('float_te'))

        self.feedback_p.SafeChangeValue('0')
        self.feedback_d.SafeChangeValue('0')
        self.feedback_i.SafeChangeValue('0')

        self.ob1_settings_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2),
            hgap=self._FromDIP(2))
        self.ob1_settings_sizer.Add(wx.StaticText(self, label='P:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ob1_settings_sizer.Add(self.feedback_p, flag=wx.ALIGN_CENTER_VERTICAL)
        self.ob1_settings_sizer.Add(wx.StaticText(self, label='I:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ob1_settings_sizer.Add(self.feedback_i, flag=wx.ALIGN_CENTER_VERTICAL)
        self.ob1_settings_sizer.Add(wx.StaticText(self, label='D:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        self.ob1_settings_sizer.Add(self.feedback_d, flag=wx.ALIGN_CENTER_VERTICAL)


        self.control_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Controls'),
            wx.VERTICAL)
        self.control_box_sizer.Add(basic_ctrl_sizer, flag=wx.EXPAND)
        self.control_box_sizer.Add(button_ctrl_sizer, flag=wx.ALIGN_CENTER_HORIZONTAL|wx.TOP,
            border=self._FromDIP(2))
        self.control_box_sizer.Add(self.ob1_ctrl_sizer, flag=wx.EXPAND|wx.TOP,
            border=self._FromDIP(2))
        self.control_box_sizer.Add(self.hamilton_ctrl_sizer, flag=wx.EXPAND|wx.TOP,
            border=self._FromDIP(2))

        self.settings_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Settings'),
            wx.VERTICAL)
        self.settings_box_sizer.Add(gen_settings_sizer, flag=wx.EXPAND)
        self.settings_box_sizer.Add(self.phd4400_settings_sizer,
            flag=wx.EXPAND|wx.TOP, border=self._FromDIP(2))
        # self.settings_box_sizer.Add(self.picoplus_settings_sizer,
        #     flag=wx.EXPAND|wx.TOP, border=self._FromDIP(2))
        self.settings_box_sizer.Add(self.ssi_settings_sizer,
            flag=wx.EXPAND|wx.TOP, border=self._FromDIP(2))
        self.settings_box_sizer.Add(self.ob1_settings_sizer,
            flag=wx.EXPAND|wx.TOP, border=self._FromDIP(2))

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.status_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.control_box_sizer, border=self._FromDIP(5),
            flag=wx.EXPAND|wx.TOP)
        top_sizer.Add(self.settings_box_sizer, border=self._FromDIP(5),
            flag=wx.EXPAND|wx.TOP)

        self.volume_lbl.Hide()
        self.volume_ctrl.Hide()
        self.vol_units_lbl.Hide()
        self.fr_button.Hide()

        self.control_box_sizer.Hide(self.ob1_ctrl_sizer, recursive=True)
        self.control_box_sizer.Hide(self.hamilton_ctrl_sizer, recursive=True)

        self.settings_box_sizer.Hide(self.phd4400_settings_sizer, recursive=True)
        # self.settings_box_sizer.Hide(self.picoplus_settings_sizer, recursive=True)
        self.settings_box_sizer.Hide(self.ssi_settings_sizer, recursive=True)
        self.settings_box_sizer.Hide(self.ob1_settings_sizer, recursive=True)

        self.status_sizer.Hide(self.ssi_status_sizer, recursive=True)

        print(self.pump_type)
        print(self.settings)

        if (self.pump_type == 'VICI M50' or self.pump_type == 'KPHM100'
            or self.pump_type == 'Longer L100S2' or self.pump_type == 'Soft'):
            self.pump_mode = 'continuous'

        elif (self.pump_type == 'PHD 4400' or self.pump_type == 'NE 500'
            or self.pump_type == 'Pico Plus' or self.pump_type == 'Hamilton PSD6'
            or self.pump_type == 'Soft Syringe'):
            self.settings_box_sizer.Show(self.phd4400_settings_sizer, recursive=True)
            self.pump_mode = 'syringe'

            # if self.pump_type == 'Pico Plus':
            #     self.settings_box_sizer.Show(self.picoplus_settings_sizer, recursive=True)

        elif self.pump_type == 'SSI Next Gen':
            self.flow_accel_lbl.Show()
            self.flow_accel_ctrl.Show()
            self.flow_accel_units_lbl.Show()

            self.status_sizer.Show(self.ssi_status_sizer, recursive=True)
            self.settings_box_sizer.Show(self.ssi_settings_sizer, recursive=True)

            self.direction_lbl.Hide()
            self.direction_ctrl.Hide()

            self.pump_mode = 'continuous'

        elif self.pump_type == 'OB1' or self.pump_type == 'OB1 Pump':
            self.status_sizer.Show(self.ssi_status_sizer, recursive=True)
            self.control_box_sizer.Show(self.ob1_ctrl_sizer, recursive=True)
            self.settings_box_sizer.Show(self.ob1_settings_sizer, recursive=True)

            self.pump_mode = 'continuous'

        if self.pump_mode == 'continuous':
            self.status_sizer.Hide(self.vol_gauge, recursive=True)
            self.syringe_volume.Hide()
            self.syringe_volume_units.Hide()
            self.syringe_volume_label.Hide()
            self.set_syringe_volume.Hide()
            self.refill_rate_ctrl.Hide()
            self.refill_rate_lbl.Hide()
            self.refill_units_lbl.Hide()

        if self.pump_type == 'Hamilton PSD6':
            self.set_syringe_volume.Hide()
            self.control_box_sizer.Show(self.hamilton_ctrl_sizer, recursive=True)


        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()
        self.flow_units_lbl.SetLabel('{}/{}'.format(vol_unit, t_unit))
        self.vol_units_lbl.SetLabel(vol_unit)
        self.syringe_volume_units.SetLabel(vol_unit)
        self.refill_units_lbl.SetLabel('{}/{}'.format(vol_unit, t_unit))
        self.flow_readback_units.SetLabel('{}/{}'.format(vol_unit, t_unit))

        pressure_unit = self.pressure_units.GetStringSelection()
        self.pressure_units_lbl.SetLabel(pressure_unit)
        self.max_pressure_units_lbl.SetLabel(pressure_unit)
        self.min_pressure_units_lbl.SetLabel(pressure_unit)

        self.Layout()
        self.Refresh()

        self.SetSizer(top_sizer)

    def _init_device(self, settings):
        device_data = settings['device_data']
        args = copy.copy(device_data['args'])
        kwargs = device_data['kwargs']
        ctrl_args = device_data['ctrl_args']

        args.insert(0, self.name)

        if (self.pump_type == 'PHD 4400' or self.pump_type == 'NE 500'
            or self.pump_type == 'Pico Plus' or self.pump_type == 'Hamilton PSD6'
            or self.pump_type =='Soft Syringe'):
            if 'syringe_id' in kwargs:
                self.syringe_type.SetStringSelection(kwargs['syringe_id'])

            else:
                kwargs['syringe_id'] = self.syringe_type.GetStringSelection()

            kwargs.update(copy.deepcopy(self.known_syringes[kwargs['syringe_id']]))
            self._update_syringe_gui_values(kwargs['syringe_id'])

            if 'dual_syringe' in kwargs:
                self.dual_syringe.SetStringSelection(kwargs['dual_syringe'])

        if self.pump_type == 'OB1 Pump':
            # ob1_device_name = kwargs.pop('ob1_device_name')
            # calib_path = kwargs.pop('calib_path')
            # get_ob1_cmd = ['get_pump', [ob1_device_name], {}]

            # ob1_device = self._send_cmd(get_ob1_cmd, get_response=True)

            # if ob1_device is None:
            #     ob1_args = [ob1_device_name, 'OB1', args[2]]
            #     ob1_kwargs = {'comm_lock': kwargs['comm_lock'],
            #         'calib_path': calib_path}

            #     cmd = ['connect', ob1_args, ob1_kwargs]

            #     self._send_cmd(cmd, get_response=False)

            #     ob1_device = self._send_cmd(get_ob1_cmd, get_response=True)

            # kwargs['ob1_device'] = ob1_device

            if 'P' in kwargs:
                self.feedback_p.SafeChangeValue(str(kwargs['P']))

            if 'I' in kwargs:
                self.feedback_i.SafeChangeValue(str(kwargs['I']))

            if 'D' in kwargs:
                self.feedback_d.SafeChangeValue(str(kwargs['D']))

        connect_cmd = ['connect', args, kwargs]

        self.connected = self._send_cmd(connect_cmd, get_response=True)

        if self.connected is None:
            self.connected = False

        if self.connected:
            self._set_status_label('Connected')

            if 'units' in self.settings['device_data']['ctrl_args']:
                units = self.settings['device_data']['ctrl_args']['units']
                units_cmd = ['set_units', [self.name, units], {}]
                self._send_cmd(units_cmd, get_response=False)

            # if self.pump_type == 'Pico_Plus':
            #     force = self.pump.force
            #     self.force.ChangeValue(str(force))

            # is_moving_cmd = ['is_moving', [self.name,], {}]
            # self._update_status_cmd(is_moving_cmd, 1)

            # get_flow_rate_cmd = ['get_flow_rate', [self.name,], {}]
            # self._update_status_cmd(get_flow_rate_cmd, 1)

            # get_flow_dir_cmd = ['get_flow_dir', [self.name,], {}]
            # self._update_status_cmd(get_flow_dir_cmd, 1)

            # get_pressure_cmd = ['get_pressure', [self.name,], {}]
            # self._update_status_cmd(get_pressure_cmd, 1)

            get_full_status_cmd = ['get_full_status', [self.name,], {}]
            self._update_status_cmd(get_full_status_cmd, 1)

            get_settings_cmd = ['get_settings', [self.name,], {}]
            self._update_status_cmd(get_settings_cmd, 5)

            # if self.pump_mode == 'syringe':
            #     get_volume_cmd = ['get_volume', [self.name,], {}]
            #     self._update_status_cmd(get_volume_cmd, 1)

            #     get_refill_rate_cmd = ['get_refill_rate', [self.name,], {}]
            #     self._update_status_cmd(get_refill_rate_cmd, 1)

            # if self.pump_type == 'Hamilton PSD6':
            #     get_valve_pos_cmd = ['get_valve_pos', [self.name,], {}]
            #     self._update_status_cmd(get_valve_pos_cmd, 5)

        logger.info('Initialized pump %s on startup', self.name)

    def _on_units(self, evt):
        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()

        new_units = '{}/{}'.format(vol_unit, t_unit)

        if new_units != self._current_units:
            self._current_units = new_units

            units_cmd = ['set_units', [self.name, new_units], {}]
            self._send_cmd(units_cmd, get_response=False)

            self._update_gui_units()

    def _update_gui_units(self):
        """Called when the units are changed in the GUI."""
        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()

        new_units = '{}/{}'.format(vol_unit, t_unit)

        old_units = self.flow_units_lbl.GetLabel()
        old_vol, old_t = old_units.split('/')

        self.flow_units_lbl.SetLabel(new_units)
        self.vol_units_lbl.SetLabel(vol_unit)
        self.syringe_volume_units.SetLabel(vol_unit)
        self.refill_units_lbl.SetLabel(new_units)
        self.flow_accel_units_lbl.SetLabel('{}/{}^2'.format(vol_unit, t_unit))
        self.flow_readback_units.SetLabel(new_units)

        try:
            flow_rate = float(self.flow_rate_ctrl.GetValue())
        except ValueError:
            flow_rate = 0

        flow_rate = convert_flow_rate(flow_rate, old_units, new_units)

        if flow_rate != 0:
            self.flow_rate_ctrl.ChangeValue('{0:.3f}'.format(flow_rate))

        try:
            refill_rate = float(self.refill_rate_ctrl.GetValue())
        except ValueError:
            refill_rate = 0

        refill_rate = convert_flow_rate(refill_rate, old_units, new_units)

        if refill_rate != 0:
            self.refill_rate_ctrl.ChangeValue('{0:.3f}'.format(refill_rate))

        try:
            flow_rate_accel = float(self.flow_accel_ctrl.GetValue())
        except ValueError:
            flow_rate_accel = 0

        flow_rate_accel = convert_flow_accel(flow_rate_accel, old_units, new_units)

        if flow_rate_accel != 0:
            self.flow_accel_ctrl.ChangeValue('{0:.3f}'.format(flow_rate_accel))

        try:
            syringe_vol = float(self.syringe_volume.GetLabel())
        except ValueError:
            syringe_vol = 0

        syringe_vol = convert_volume(syringe_vol, old_vol, vol_unit)

        if syringe_vol != 0:
            self.syringe_volume.SetLabel('{0:.3f}'.format(syringe_vol))


        try:
            syringe_vol_gauge_high = float(self.syringe_vol_gauge_high.GetLabel())
        except ValueError:
            syringe_vol_gauge_high = 0

        syringe_vol_gauge_high = convert_volume(syringe_vol_gauge_high, old_vol, vol_unit)

        if syringe_vol_gauge_high != 0:
            self.syringe_vol_gauge_high.SetLabel('{0:.3f}'.format(syringe_vol_gauge_high))

        try:
            vol_gauge_max = self.syringe_vol_gauge.GetRange()
        except ValueError:
            vol_gauge_max = 0

        vol_gauge_max = convert_volume(vol_gauge_max, old_vol, vol_unit)

        self.syringe_vol_gauge.SetRange(int(round(float(vol_gauge_max))))

        try:
            syringe_vol_gauge = float(self.syringe_vol_gauge.GetValue())
        except ValueError:
            syringe_vol_gauge = 0

        syringe_vol_gauge = convert_volume(syringe_vol_gauge, old_vol, vol_unit)

        if syringe_vol_gauge != 0:
            self.syringe_vol_gauge.SetValue(int(round(syringe_vol_gauge)))

        try:
            volume = float(self.volume_ctrl.GetValue())
        except ValueError:
            volume = 0

        volume = convert_volume(volume, old_vol, vol_unit)

        if volume != 0:
            self.volume_ctrl.ChangeValue('{0:.3f}'.format(volume))



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
                    logger.info('Failed to set pump %s flow rate', self.name)
                    return

                mode = self.mode_ctrl.GetStringSelection()
                units = self.vol_units_lbl.GetLabel()

                if mode == 'Fixed volume':
                    try:
                        vol = float(self.volume_ctrl.GetValue())
                    except Exception:
                        msg = "Volume must be a number."
                        wx.MessageBox(msg, "Error setting volume")
                        logger.debug('Failed to set dispense/aspirate volume for pump %s', self.name)
                        return

                logger.info('Starting pump %s flow', self.name)
                if self.pump_mode == 'continuous':
                    if mode == 'Fixed volume':
                        pump_dir = self.direction_ctrl.GetStringSelection().lower()
                        cmd = [pump_dir, [self.name, vol], {'units': units}]
                        self._send_cmd(cmd, get_response=False)
                    else:
                        cmd = ['start_flow', [self.name,], {}]
                        self._send_cmd(cmd, get_response=False)
                else:
                    if mode == 'Fixed volume':
                        pump_dir = self.direction_ctrl.GetStringSelection().lower()
                        cmd = [pump_dir, [self.name, vol], {'units': units}]
                        self._send_cmd(cmd, get_response=False)
                    else:
                        pump_dir = self.direction_ctrl.GetStringSelection().lower()
                        cmd = ['{}_all'.format(pump_dir), [self.name,], {}]
                        self._send_cmd(cmd, get_response=False)

            else:
                logger.info('Stopping pump %s flow', self.name)
                self._set_flowaccel()

                cmd = ['stop', [self.name,], {}]
                self._send_cmd(cmd, get_response=False)

        else:
            msg = "Cannot start pump flow before the pump is connected."
            wx.MessageBox(msg, "Error starting flow")
            logger.debug('Failed to start flow for pump %s because it is not connected', self.name)

    def _on_fr_change(self, evt):
        """Called when the flow rate is started or stopped in the GUI."""
        self._set_flowrate()

    def _on_set_volume(self, evt):
        wx.CallAfter(self._set_volume)

    def _set_volume(self):
        vol = wx.GetTextFromUser("Enter current syringe volume:",
            "Set Syringe Volume", "0", parent=self)

        if vol != '':
            try:
                vol = float(vol)
                if vol != -1:
                    cmd = ['set_volume', [self.name, vol], {}]
                    self._send_cmd(cmd, get_response=False)

            except ValueError:
                msg = "Volume must be a number."
                wx.MessageBox(msg, "Error setting volume")


    def _on_syringe_type(self, evt):
        new_syringe = self.syringe_type.GetStringSelection()

        kwargs = copy.deepcopy(self.known_syringes[new_syringe])
        kwargs['syringe_id'] = new_syringe

        if self.connected:
            cmd = ['set_pump_cal', [self.name,], kwargs]
            self._send_cmd(cmd, get_response=False)

        self._update_syringe_gui_values(new_syringe)

    def _update_syringe_gui_values(self, new_syringe):
        max_vol = self.known_syringes[new_syringe]['max_volume']
        vol_unit = self.vol_unit_ctrl.GetStringSelection()

        max_vol = convert_volume(max_vol, 'mL', vol_unit)

        self.syringe_vol_gauge_high.SetLabel(str(max_vol))
        self.syringe_vol_gauge.SetRange(int(round(float(max_vol)*1000)))

    # def _on_force_change(self, obj, value):
    #     value = int(value)
    #     cmd = ['set_force', [self.name, value], {}]
    #     self._send_cmd(cmd, get_response=True)

    def _on_valve_change(self, evt):
        value = self.valve_ctrl.GetStringSelection()
        cmd = ['set_valve_pos', [self.name, value], {}]
        self._send_cmd(cmd, get_response=False)

    def _on_max_pressure_change(self, obj, value):
        value = float(value)
        cmd = ['set_max_pressure', [self.name, value], {}]
        self._send_cmd(cmd, get_response=False)

    def _on_min_pressure_change(self, obj, value):
        value = float(value)
        cmd = ['set_min_pressure', [self.name, value], {}]
        self._send_cmd(cmd, get_response=False)

    def _on_pressure_units(self, evt):
        units = self.pressure_units.GetStringSelection()
        self._current_pressure_units = units
        cmd = ['set_pressure_units', [self.name, units], {}]
        self._send_cmd(cmd, get_response=False)

        self._set_pressure_units_gui(units)

    def _on_set_pressure(self, obj, value):
        value = float(value)
        cmd = ['set_pressure', [self.name, value], {}]
        self._send_cmd(cmd, get_response=False)

    def _on_pid_change(self, obj, value):
        P = self.feedback_p.GetValue()
        I = self.feedback_i.GetValue()
        D = self.feedback_d.GetValue()

        P = float(P)
        I = float(I)
        D = float(D)

        cmd = ['set_pid', [self.name, P, I, D], {}]
        self._send_cmd(cmd, get_response=False)

    def _set_pressure_units_gui(self, units):
        self.pressure_units_lbl.SetLabel(units)
        self.max_pressure_units_lbl.SetLabel(units)
        self.min_pressure_units_lbl.SetLabel(units)

    def _set_status_label(self, status):
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
        units = self.flow_units_lbl.GetLabel()
        units_cmd = ['set_units', [self.name, units], {}]
        self._send_cmd(units_cmd, get_response=False)

        self._set_flowaccel()

        pump_dir = self.direction_ctrl.GetStringSelection().lower()
        if self.pump_mode == 'continuous':
            if pump_dir == 'dispense':
                mult = 1
            else:
                mult = -1
        else:
            mult = 1

        if self.pump_type == 'NE 500':
            if pump_dir == 'Dispense':
                try:
                    fr = float(self.flow_rate_ctrl.GetValue())

                    set_fr_cmd = ['set_flow_rate', [self.name, fr*mult], {}]
                    ret = self._send_cmd(set_fr_cmd, get_response=True)

                    if ret is not None and ret:
                        success = True
                    else:
                        success = False

                except Exception:
                    msg = "Flow rate must be a number."
                    wx.MessageBox(msg, "Error setting flow rate")
                    success = False

            else:
                try:
                    rr = float(self.refill_rate_ctrl.GetValue())

                    set_rr_cmd = ['set_refill_rate', [self.name, rr*mult], {}]
                    ret = self._send_cmd(set_rr_cmd, get_response=True)

                    if ret is not None and ret:
                        success = True
                    else:
                        success = False

                except Exception:
                    msg = "Refill rate must be a number."
                    wx.MessageBox(msg, "Error setting refill rate")
                    success = False
        else:
            try:
                fr = float(self.flow_rate_ctrl.GetValue())

                set_fr_cmd = ['set_flow_rate', [self.name, fr*mult], {}]
                ret = self._send_cmd(set_fr_cmd, get_response=True)

                if ret is not None and ret:
                    success = True
                else:
                    success = False

            except Exception:
                msg = "Flow rate must be a number."
                wx.MessageBox(msg, "Error setting flow rate")
                success = False

            if success and self.pump_mode == 'syringe':
                try:
                    rr = float(self.refill_rate_ctrl.GetValue())

                    rr = float(self.refill_rate_ctrl.GetValue())

                    set_rr_cmd = ['set_refill_rate', [self.name, rr*mult], {}]
                    ret = self._send_cmd(set_rr_cmd, get_response=True)

                    if ret is not None and ret:
                        success = True
                    else:
                        success = False

                except Exception:
                    msg = "Refill rate must be a number."
                    wx.MessageBox(msg, "Error setting refill rate")
                    success = False

        if success:
            logger.debug('Set pump %s flow rate to %s', self.name, str(fr))
        else:
            logger.debug('Failed to set pump %s flow rate and/or refill rate', self.name)

        return success

    def _set_flowaccel(self):
        if self.pump_type == 'SSI Next Gen':
            try:
                fr = float(self.flow_accel_ctrl.GetValue())

                cmd = ['set_flow_accel', [self.name, fr], {}]
                ret = self._send_cmd(cmd, get_response=True)

                if ret is not None and ret:
                    success = True
                else:
                    success = False


            except Exception:
                msg = "Flow rate acceleration must be a number."
                wx.MessageBox(msg, "Error setting flow rate acceleration")
                success = False

            if success:
                logger.debug('Set pump %s flow accelration to %s', self.name, str(fr))
            else:
                logger.debug('Failed to set pump %s flow rate acceleration', self.name)

    def _set_status(self, cmd, val):
        if cmd == 'get_full_status':
            is_moving = val['is_moving']
            flow_rate = val['flow_rate']
            flow_dir = val['flow_dir']
            pressure = val['pressure']
            volume = val['volume']
            refill_rate = val['refill_rate']
            valve_pos = val['valve_pos']

            if not self._current_move_status:
                flow_rate = 0
                refill_rate = 0

            if is_moving is not None and is_moving and not self._current_move_status:
                self.run_button.SetLabel('Stop')
                if self.pump_type != 'Hamilton PSD6':
                    self.fr_button.Show()

                if self.pump_mode == 'continuous':
                    if self.mode_ctrl.GetStringSelection() == 'Fixed volume':
                        if self._current_flow_dir == 1:
                            pump_dir = 'Dispense'
                        else:
                            pump_dir = 'Aspirate'
                        self._set_status_label(pump_dir.capitalize())
                    else:
                        self._set_status_label('Flowing')
                else:
                    if self._current_flow_dir == 1:
                        pump_dir ='Dispense'
                    else:
                        pump_dir = 'Aspirate'
                    self._set_status_label(pump_dir.capitalize())

                self._current_move_status = val

            elif is_moving is not None and not is_moving and self._current_move_status:
                self.run_button.SetLabel('Start')
                if self.pump_type != 'Hamilton PSD6':
                    self.fr_button.Hide()

                self._set_status_label('Done')

                self._current_move_status = is_moving

                if self.pump_mode == 'syringe':
                    stop_cmd = ['stop', [self.name,], {}]
                    self._send_cmd(stop_cmd, get_response=False)

            if volume is not None and volume != self._current_volume:
                self._set_status_volume(volume)
                self.syringe_vol_gauge.SetValue(int(round(float(volume)*1000)))
                self._current_volume = volume



            if flow_rate is not None and round(flow_rate, 4) != float(self.flow_readback.GetLabel()):
                self._current_flow_rate = flow_rate

                if self.pump_mode =='continuous' or self._current_flow_dir >= 0:
                    wx.CallAfter(self.flow_readback.SetLabel, str(round(flow_rate, 4)))

            if refill_rate is not None and round(refill_rate, 4) != float(self.flow_readback.GetLabel()):
                self._current_refill_rate = refill_rate

                if self.pump_mode == 'syringe' and self._current_flow_dir < 0:
                    wx.CallAfter(self.flow_readback.SetLabel, str(round(refill_rate, 4)))

            if flow_dir is not None:
                if self._current_flow_dir != flow_dir:
                    if self._current_move_status:
                        if self.pump_mode == 'continuous':
                            if self.mode_ctrl.GetStringSelection() == 'Fixed volume':
                                if flow_dir == 1:
                                    pump_dir = 'Dispense'
                                else:
                                    pump_dir = 'Aspirate'
                                self._set_status_label(pump_dir.capitalize())
                            else:
                                self._set_status_label('Flowing')
                        else:
                            if flow_dir == 1:
                                pump_dir ='Dispense'
                            else:
                                pump_dir = 'Aspirate'
                            self._set_status_label(pump_dir.capitalize())

                self._current_flow_dir = flow_dir

            if pressure is not None and pressure != self._current_pressure:
                self.pressure.SetLabel(str(pressure))
                self._current_pressure = pressure

            if valve_pos is not None and valve_pos != self._current_valve_position:
                self.valve_ctrl.SetStringSelection(str(valve_pos))
                self._current_valve_position = valve_pos

        elif cmd == 'get_settings':
            if val is not None:
                min_pressure = val['min_pressure']
                max_pressure = val['max_pressure']
                pressure_units = val['pressure_units']
                units = val['units']
                faults = val['faults']
                syringe_id = val['syringe_id']

                if min_pressure is not None:
                    if min_pressure != self._current_min_pressure:
                        self._current_min_pressure = min_pressure
                        self.min_pressure.SafeChangeValue(str(min_pressure))

                if max_pressure is not None:
                    if max_pressure != self._current_max_pressure:
                        self._current_max_pressure = max_pressure
                        self.max_pressure.SafeChangeValue(str(max_pressure))

                if pressure_units is not None:
                    if pressure_units != self._current_pressure_units:
                        self._current_pressure_units = pressure_units
                        self._set_pressure_units_gui(pressure_units)
                        self.pressure_units.SetStringSelection(pressure_units)

                if units is not None:
                    if units != self._current_units:
                        vu, tu = units.split('/')
                        self.vol_unit_ctrl.SetStringSelection(vu)
                        self.time_unit_ctrl.SetStringSelection(tu)

                        self._current_units = units

                        self._update_gui_units()

                if faults is not None:
                    self._check_faults(faults)

                if syringe_id is not None:
                    if syringe_id != self.syringe_type.GetStringSelection():
                        self.syringe_type.SetStringSelection(syringe_id)
                        self._update_syringe_gui_values(syringe_id)

    def _check_faults(self, faults):
        fault_list = []
        for fault, status in faults.items():
            if status:
                fault_list.append(fault)

        if len(fault_list) > 0:
            msg = ('Pump {} has the following faults:'.format(self.name))

            for fault in fault_list:
                msg = msg + '\n-{}'.format(fault)

            dialog = wx.MessageDialog(self,
                caption='Pump {} Faults'.format(self.name), message=msg,
                style=wx.OK|wx.CANCEL|wx.OK_DEFAULT|wx.ICON_ERROR)

            dialog.SetOKCancelLabels('Clear faults', 'Proceed without clearing')
            ret = dialog.ShowModal()

            if ret == wx.ID_OK:
                cmd = ['clear_faults', [self.name,], {}]
                self._send_cmd(cmd, get_response=False)

class PumpFrame(utils.DeviceFrame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, name, settings, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(PumpFrame, self).__init__(name, settings, PumpPanel, *args, **kwargs)

        # Enable these to init devices on startup
        self.setup_devices = self.settings.pop('device_init', None)
        self._init_devices()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    # h1.setLevel(logging.DEBUG)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.WARNING)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    # my_pump = M50Pump('COM6', '2')

    # my_pump = SSINextGenPump('COM15', 'test')

    # my_pump = LongerL1001S2Pump('test', 'COM9', 1)

    comm_lock = threading.Lock()

    # my_pump = PHD4400Pump('COM4', 'H1', '1', 23.5, 30, 30, '30 mL', comm_lock)
    # my_pump.flow_rate = 10
    # my_pump.refill_rate = 10

    # my_pump2 = PHD4400Pump('COM4', 'H2', '2', 23.5, 30, 30, '30 mL', comm_lock)
    # my_pump2.flow_rate = 10
    # my_pump2.refill_rate = 10

    # my_pump = PicoPlusPump('/dev/cu.usbmodem11085401', 'PicoPlusTest', '0', 12.8, 6,
    #     23, '6 mL, Medline P.C.', False, comm_lock)
    # my_pump.flow_rate = 5
    # my_pump.refill_rate = 5

    # my_pump = NE500Pump('/dev/cu.usbserial-A6022U22', 'Pump2', '00', 23.5, 30, 30, '30 mL', comm_lock)
    # my_pump.flow_rate = 10
    # my_pump.refill_rate = 10

    # my_pump = HamiltonPSD6Pump('Pump1', 'COM7', '1', 1.46, 0.1, 1,
    #     '0.1 mL, Hamilton Glass', False, comm_lock=comm_lock)
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

    # Coflow pumps
    # setup_devices = [
    #     {'name': 'sheath', 'args': ['VICI M50', 'COM4'],
    #         'kwargs': {'flow_cal': '627.72', 'backlash_cal': '9.814'},
    #         'ctrl_args': {'flow_rate': 1}},
    #     {'name': 'outlet', 'args': ['VICI M50', 'COM5'],
    #         'kwargs': {'flow_cal': '628.68', 'backlash_cal': '9.962'},
    #         'ctrl_args': {'flow_rate': 1}},
    #     ]

    # # Batch mode pumps
    # setup_devices = [
    #     {'name': 'water', 'args': ['KPHM100', 'COM10'],
    #         'kwargs': {'flow_cal': '319.2',},
    #         'ctrl_args': {'flow_rate': 1}},
    #     {'name': 'ethanol', 'args': ['KPHM100', 'COM8'],
    #         'kwargs': {'flow_cal': '319.2',},
    #         'ctrl_args': {'flow_rate': 1}},
    #     {'name': 'hellmanex', 'args': ['KPHM100', 'COM9'],
    #         'kwargs': {'flow_cal': '319.2',},
    #         'ctrl_args': {'flow_rate': 1}},
    #     {'name': 'Sample', 'args': ['Hamilton PSD6', 'COM12'],
    #         'kwargs': {'syringe_id': '0.1 mL, Hamilton Glass',
    #          'pump_address': '1', 'dual_syringe': 'False'},
    #         'ctrl_args': {'flow_rate' : '1', 'refill_rate' : '1'}},
    #     ]



    # # Coflow with OB1
    # bfs = fmcon.BFS('outlet_fm', 'COM3')
    # bfs.start_remote()

    # ob1_comm_lock = threading.RLock()

    # setup_devices = [
    #     {'name': 'sheath', 'args': ['VICI M50', 'COM6'],
    #         'kwargs': {'flow_cal': '627.72', 'backlash_cal': '9.814'},
    #         'ctrl_args': {'flow_rate': 1}},
    #     {'name': 'outlet', 'args': ['OB1 Pump', 'COM15'],
    #         'kwargs': {'ob1_device_name': 'Outlet OB1', 'channel': 1,
    #         'min_pressure': -1000, 'max_pressure': 1000, 'P': -2, 'I': -0.15,
    #         'D': 0, 'bfs_instr_ID': bfs.instr_ID, 'comm_lock': ob1_comm_lock,
    #         'calib_path': './resources/ob1_calib.txt'},
    #         'ctrl_args': {}}
    #     ]

    # OB1 by itself
    # bfs = fmcon.BFS('outlet_fm', 'COM5')

    # ob1_comm_lock = threading.RLock()

    # setup_devices = [
    #     {'name': 'outlet', 'args': ['OB1 Pump', 'COM8'],
    #         'kwargs': {'ob1_device_name': 'Outlet OB1', 'channel': 1,
    #         'min_pressure': -1000, 'max_pressure': 1000, 'P': 8, 'I': 2,
    #         'D': 0, 'bfs_instr_ID': bfs.instr_ID, 'comm_lock': ob1_comm_lock,
    #         'calib_path': './resources/ob1_calib.txt'},
    #         'ctrl_args': {}}
    #     ]

    # # TR-SAXS PHD 4400 pumps
    # setup_devices = [
    #     {'name': 'Sample', 'args': ['PHD 4400', 'COM4'],
    #         'kwargs': {'syringe_id': '10 mL, Medline P.C.', 'pump_address': '1'},
    #         'ctrl_args': {'flow_rate' : '10', 'refill_rate' : '10'}},
    #     {'name': 'Buffer 1', 'args': ['PHD 4400', 'COM4'],
    #         'kwargs': {'syringe_id': '20 mL, Medline P.C.', 'pump_address': '2'},
    #         'ctrl_args': {'flow_rate' : '10', 'refill_rate' : '10'}},
    #     {'name': 'Buffer 2', 'args': ['PHD 4400', 'COM4'],
    #         'kwargs': {'syringe_id': '20 mL, Medline P.C.', 'pump_address': '3'},
    #         'ctrl_args': {'flow_rate' : '10', 'refill_rate' : '10'}},
    #     ]

    # # TR-SAXS NE 500 pumps
    # setup_devices = [
    #     {'name': 'Buffer', 'args': ['NE 500', 'COM11'],
    #         'kwargs': {'syringe_id': '20 mL, Medline P.C.', 'pump_address': '00'},
    #         'ctrl_args': {'flow_rate' : '0.1', 'refill_rate' : '10'}},
    #     {'name': 'Sheath', 'args': ['NE 500', 'COM10'],
    #         'kwargs': {'syringe_id': '20 mL, Medline P.C.', 'pump_address': '00'},
    #         'ctrl_args': {'flow_rate' : '0.1', 'refill_rate' : '10'}},
    #     {'name': 'Sample', 'args': ['NE 500', 'COM3'],
    #         'kwargs': {'syringe_id': '20 mL, Medline P.C.', 'pump_address': '00'},
    #         'ctrl_args': {'flow_rate' : '0.1', 'refill_rate' : '10'}},
    #     ]

    # Teledyne SSI Reaxus pumps with scaling
    # setup_devices = [
    #     {'name': 'Pump 4', 'args': ['SSI Next Gen', 'COM14'],
    #         'kwargs': {'flow_rate_scale': 1.0583,
    #         'flow_rate_offset': -33.462/1000,'scale_type': 'up'},
    #         'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.1}},
    #     {'name': 'Pump 3', 'args': ['SSI Next Gen', 'COM17'],
    #         'kwargs': {'flow_rate_scale': 1.0135,
    #         'flow_rate_offset': 5.1251/1000,'scale_type': 'up'},
    #         'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.1}},
    #     {'name': 'Pump 2', 'args': ['SSI Next Gen', 'COM18'],
    #         'kwargs': {'flow_rate_scale': 1.0497,
    #         'flow_rate_offset': -34.853/1000,'scale_type': 'up'},
    #         'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.1}},
    #      ]

    # # Teledyne SSI Reaxus pumps without scaling
    # setup_devices = [
    #     {'name': 'Pump 4', 'args': ['SSI Next Gen', 'COM19'],
    #         'kwargs': {'flow_rate_scale': 1,
    #         'flow_rate_offset': 0,'scale_type': 'up'},
    #         'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.1}},
    #     {'name': 'Pump 3', 'args': ['SSI Next Gen', 'COM14'],
    #         'kwargs': {'flow_rate_scale': 1,
    #         'flow_rate_offset': 0,'scale_type': 'up'},
    #         'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.1}},
    #     {'name': 'Pump 2', 'args': ['SSI Next Gen', 'COM18'],
    #         'kwargs': {'flow_rate_scale': 1,
    #         'flow_rate_offset': 0,'scale_type': 'up'},
    #         'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.1}},
    #     ]

    # # SEC-SAXS pump, Teledyne SSI Reaxus pumps without scaling
    # setup_devices = [
    #     {'name': 'Pump 1', 'args': ['SSI Next Gen', 'COM3'],
    #         'kwargs': {'flow_rate_scale': 1,
    #         'flow_rate_offset': 0,'scale_type': 'up'},
    #         'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.1}},
    #     ]

    # # TR-SAXS Pico Plus pumps
    # setup_devices = [
    #     {'name': 'Buffer', 'args': ['Pico Plus', 'COM11'],
    #         'kwargs': {'syringe_id': '3 mL, Medline P.C.',
    #         'pump_address': '00', 'dual_syringe': 'False'},
    #         'ctrl_args': {'flow_rate' : '1', 'refill_rate' : '1'}},
    #     {'name': 'Sample', 'args': ['Pico Plus', 'COM9'],
    #         'kwargs': {'syringe_id': '1 mL, Medline P.C.',
    #          'pump_address': '00', 'dual_syringe': 'False'},
    #         'ctrl_args': {'flow_rate' : '1', 'refill_rate' : '1'}},
    #     {'name': 'Sheath', 'args': ['Pico Plus', 'COM7'],
    #         'kwargs': {'syringe_id': '1 mL, Medline P.C.',
    #          'pump_address': '00', 'dual_syringe': 'False'},
    #         'ctrl_args': {'flow_rate' : '1', 'refill_rate' : '1'}},
    #     ]

    # # Batch mode Hamilton PSD6 pump
    # setup_devices = [
    #     {'name': 'sample', 'args': ['Hamilton PSD6', 'COM9'],
    #         'kwargs': {'syringe_id': '0.1 mL, Hamilton Glass',
    #         'pump_address': '1', 'dual_syringe': 'False',
    #         'diameter': 1.46, 'max_volume': 0.1,
    #         'max_rate': 1, 'comm_lock': threading.RLock(),},
    #         'ctrl_args': {'flow_rate' : 100,
    #         'refill_rate' : 100, 'units': 'uL/min'}},
    #     ]

    # # Simulated pumps
    # setup_devices = [
    #     {'name': 'Soft', 'args': ['Soft', None], 'kwargs': {},
    #         'ctrl_args': {'flow_rate': 1, 'refill_rate': 1}},
    #     {'name': 'Sample', 'args': ['Soft Syringe', None],
    #         'kwargs': {'syringe_id': '3 mL, Medline P.C.',},
    #         'ctrl_args': {'flow_rate': 1, 'refill_rate': 1}},
    #     ]

    # # Simulated coflow pumps
    # setup_devices = [
    #     {'name': 'sheath', 'args': ['Soft', None], 'kwargs': {}},
    #     {'name': 'outlet', 'args': ['Soft', None], 'kwargs': {}},
    #     ]

    # Longer pumps
    setup_devices = [
        {'name': 'sheath', 'args': ['Longer L100S2', 'COM9'],
            'kwargs': {'pump_addr': 1, 'flow_cal': '1'},
            'ctrl_args': {'flow_rate': 1}},
        ]

    # Local
    com_thread = PumpCommThread('PumpComm')
    com_thread.start()

    # # Remote
    # com_thread = None

    settings = {
        'remote'        : False,
        'remote_device' : 'pump',
        'device_init'   : setup_devices,
        'remote_ip'     : '164.54.204.24',
        'remote_port'   : '5556',
        'com_thread'    : com_thread
        }

    app = wx.App()
    logger.debug('Setting up wx app')
    frame = PumpFrame('PumpFrame', settings, parent=None, title='Pump Control')
    frame.Show()
    app.MainLoop()

    if com_thread is not None:
        com_thread.stop()
        com_thread.join()
