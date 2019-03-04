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

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import serial
import serial.tools.list_ports as list_ports
import wx

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
        logger.debug("Serial device on port %s returned %s", self.ser.port, ret.decode())

        return ret.decode()

    def read_all(self):
        """
        This wraps the Serial.read() function, and returns all of the
        waiting bytes.

        :returns: The ascii (decoded) value of the ``Serial.read()``
        :rtype: str
        """
        with self.ser as s:
            ret = s.read(s.in_waiting())

        logger.debug("Read all waiting bytes from serial device on port %s", self.ser.port)
        logger.debug("Serial device on port %s returned %s", self.ser.port, ret.decode())

        return ret.decode()

    def write(self, data, get_response=False, term_char='>'):
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
        if isinstance(data, str):
            if not data.endswith('\r\n'):
                data += '\r\n'
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

    def __init__(self, device, name, flow_cal=628., backlash_cal=1.5):
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

        self.pump_comm = MForceSerialComm(device)

        #Make sure parameters are set right
        self.send_cmd('EM 0') #Echo mode to full duplex
        self.send_cmd('MS 256') #Microstepping to 256, MForce default
        self.send_cmd('VI 1000') #Initial velocity to 1000, MForce default
        self.send_cmd('A 1000000') #Acceleration to 1000000, MForce default
        self.send_cmd('D 1000000') #Deceleration to 1000000, MForce default
        self.send_cmd('HC 5') #Hold current to 5%, MForce default
        self.send_cmd('RC 25') #Run current to 25%, MForce default
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

        ret = self.pump_comm.write(cmd, get_response)

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
            logger.debug("Stopping pump %s current motion before starting continuous flow", self.name)
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

    def __init__(self, command_queue, answer_queue, abort_event, name=None):
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
        self.answer_queue = answer_queue
        self._abort_event = abort_event
        self._stop_event = threading.Event()

        self._commands = {'connect'     : self._connect_pump,
                        'set_flow_rate' : self._set_flow_rate,
                        'set_units'     : self._set_units,
                        'start_flow'    : self._start_flow,
                        'stop'          : self._stop_flow,
                        'aspirate'      : self._aspirate,
                        'dispense'      : self._dispense,
                        'is_moving'     : self._is_moving,
                        'send_cmd'      : self._send_cmd,
                        'disconnect'    : self._disconnect_pump,
                        }

        self._connected_pumps = OrderedDict()

        self.known_pumps = {'VICI_M50' : M50Pump,
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

                    if command == 'connect' or command == 'disconnect':
                        self.answer_queue.append(False)

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
        self.answer_queue.append(True)
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
        self.answer_queue.append(True)
        logger.debug("Pump %s disconnected", name)

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

    def _start_flow(self, name):
        """
        This method starts continuous flow for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        logger.info("Starting pump %s continuous flow", name)
        pump = self._connected_pumps[name]
        pump.start_flow()
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
        logger.debug("Pump %s stopped", name)

    def _aspirate(self, name, vol, units='uL'):
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
        logger.debug("Pump %s aspiration started", name)

    def _dispense(self, name, vol, units='uL'):
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
        logger.info("Checking if pump %s is moving", name)
        pump = self._connected_pumps[name]
        is_moving = pump.is_moving()
        self.answer_queue.append(is_moving)
        logger.debug("Pump %s is moving: %s", name, str(is_moving))

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
        pump_args=[], pump_kwargs={}):
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

        """

        wx.Panel.__init__(self, parent, panel_id, name=panel_name)
        logger.debug('Initializing PumpPanel for pump %s', pump_name)

        self.name = pump_name
        self.pump_cmd_q = pump_cmd_q
        self.all_comports = all_comports
        self.known_pumps = known_pumps
        self.answer_q = pump_answer_q
        self.connected = False

        self.top_sizer = self._create_layout()

        self._flow_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_flow_timer, self._flow_timer)

        self.SetSizer(self.top_sizer)

        self._initpump(pump_type, comport, pump_args, pump_kwargs)


    def _create_layout(self):
        """Creates the layout for the panel."""
        self.status = wx.StaticText(self, label='Not connected')

        status_grid = wx.FlexGridSizer(rows=2, cols=2, vgap=2, hgap=2)
        status_grid.AddGrowableCol(1)
        status_grid.Add(wx.StaticText(self, label='Pump name:'))
        status_grid.Add(wx.StaticText(self, label=self.name), 1, wx.EXPAND)
        status_grid.Add(wx.StaticText(self, label='Status: '))
        status_grid.Add(self.status, 1, wx.EXPAND)

        status_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Info'),
            wx.VERTICAL)
        status_sizer.Add(status_grid, 1, wx.EXPAND)


        self.mode_ctrl = wx.Choice(self, choices=['Continuous flow', 'Fixed volume'])
        self.mode_ctrl.SetSelection(0)
        self.direction_ctrl = wx.Choice(self, choices=['Dispense', 'Aspirate'])
        self.direction_ctrl.SetSelection(0)
        self.flow_rate_ctrl = wx.TextCtrl(self)
        self.flow_units_lbl = wx.StaticText(self, label='uL/min')
        self.volume_lbl = wx.StaticText(self, label='Volume:')
        self.volume_ctrl = wx.TextCtrl(self)
        self.vol_units_lbl = wx.StaticText(self, label='uL')

        self.mode_ctrl.Bind(wx.EVT_CHOICE, self._on_mode)

        basic_ctrl_sizer = wx.GridBagSizer(vgap=2, hgap=2)
        basic_ctrl_sizer.Add(wx.StaticText(self, label='Mode:'), (0,0))
        basic_ctrl_sizer.Add(self.mode_ctrl, (0,1), span=(1,2), flag=wx.EXPAND)
        basic_ctrl_sizer.Add(wx.StaticText(self, label='Direction:'), (1,0))
        basic_ctrl_sizer.Add(self.direction_ctrl, (1,1), span=(1,2), flag=wx.EXPAND)
        basic_ctrl_sizer.Add(wx.StaticText(self, label='Flow rate:'), (2,0))
        basic_ctrl_sizer.Add(self.flow_rate_ctrl, (2,1), flag=wx.EXPAND)
        basic_ctrl_sizer.Add(self.flow_units_lbl, (2,2))
        basic_ctrl_sizer.Add(self.volume_lbl, (3,0), flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        basic_ctrl_sizer.Add(self.volume_ctrl, (3,1), flag=wx.EXPAND|wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        basic_ctrl_sizer.Add(self.vol_units_lbl, (3,2), flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
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
        self.vol_unit_ctrl.SetSelection(1)
        self.time_unit_ctrl = wx.Choice(self, choices=['s', 'min'])
        self.time_unit_ctrl.SetSelection(1)

        self.type_ctrl.Bind(wx.EVT_CHOICE, self._on_type)
        self.vol_unit_ctrl.Bind(wx.EVT_CHOICE, self._on_units)
        self.time_unit_ctrl.Bind(wx.EVT_CHOICE, self._on_units)

        gen_settings_sizer = wx.FlexGridSizer(rows=4, cols=2, vgap=2, hgap=2)
        gen_settings_sizer.AddGrowableCol(1)
        gen_settings_sizer.Add(wx.StaticText(self, label='Pump type:'))
        gen_settings_sizer.Add(self.type_ctrl, 1, wx.EXPAND)
        gen_settings_sizer.Add(wx.StaticText(self, label='COM port:'))
        gen_settings_sizer.Add(self.com_ctrl, 1, wx.EXPAND)
        gen_settings_sizer.Add(wx.StaticText(self, label='Volume unit:'))
        gen_settings_sizer.Add(self.vol_unit_ctrl)
        gen_settings_sizer.Add(wx.StaticText(self, label='Time unit:'))
        gen_settings_sizer.Add(self.time_unit_ctrl)


        self.m50_fcal = wx.TextCtrl(self, value='628')
        self.m50_bcal = wx.TextCtrl(self, value='1.5')

        self.m50_settings_sizer = wx.FlexGridSizer(rows=2, cols=3, vgap=2, hgap=2)
        self.m50_settings_sizer.AddGrowableCol(1)
        self.m50_settings_sizer.Add(wx.StaticText(self, label='Flow Cal.:'))
        self.m50_settings_sizer.Add(self.m50_fcal,1, wx.EXPAND)
        self.m50_settings_sizer.Add(wx.StaticText(self, label='uL/rev.'))
        self.m50_settings_sizer.Add(wx.StaticText(self, label='Backlash:'))
        self.m50_settings_sizer.Add(self.m50_bcal, 1, wx.EXPAND)
        self.m50_settings_sizer.Add(wx.StaticText(self, label='uL'))


        self.connect_button = wx.Button(self, label='Connect')
        self.connect_button.Bind(wx.EVT_BUTTON, self._on_connect)


        self.control_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Controls'),
            wx.VERTICAL)
        self.control_box_sizer.Add(basic_ctrl_sizer, flag=wx.EXPAND)
        self.control_box_sizer.Add(button_ctrl_sizer, flag=wx.ALIGN_CENTER_HORIZONTAL|wx.TOP, border=2)

        settings_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Settings'),
            wx.VERTICAL)
        settings_box_sizer.Add(gen_settings_sizer, flag=wx.EXPAND)
        settings_box_sizer.Add(self.m50_settings_sizer, flag=wx.EXPAND|wx.TOP, border=2)
        settings_box_sizer.Add(self.connect_button, flag=wx.ALIGN_CENTER_HORIZONTAL|wx.TOP, border=2)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(status_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.control_box_sizer, flag=wx.EXPAND)
        top_sizer.Add(settings_box_sizer, flag=wx.EXPAND)

        self.volume_lbl.Hide()
        self.volume_ctrl.Hide()
        self.vol_units_lbl.Hide()
        self.fr_button.Hide()

        if self.type_ctrl.GetStringSelection() != 'VICI M50':
            self.control_box_sizer.Hide(self.m50_settings_sizer, recursive=True)

        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()
        self.flow_units_lbl.SetLabel('{}/{}'.format(vol_unit, t_unit))
        self.vol_units_lbl.SetLabel(vol_unit)
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
            self.type_ctrl.SetStringSelection(pump_type)

        if comport in self.all_comports:
            self.com_ctrl.SetStringSelection(comport)

        if pump_type == 'VICI M50':
            if 'flow_cal' in pump_kwargs.keys():
                self.m50_fcal.ChangeValue(pump_kwargs['flow_cal'])
            if 'backlash' in pump_kwargs.keys():
                self.m50_bcal.ChangeValue(pump_kwargs['backlash'])

            if len(pump_args) >= 1:
                self.m50_fcal.ChangeValue(pump_args[0])
            if len(pump_args) == 2:
                self.m50_bcal.ChangeValue(pump_args[1])

        if pump_type in my_pumps and comport in self.all_comports:
            logger.info('Initialized pump %s on startup', self.name)
            self._connect()

    def _on_type(self, evt):
        """Called when the pump type is changed in the GUI."""
        pump = self.type_ctrl.GetStringSelection()
        logger.info('Changed the pump type to %s for pump %s', pump, self.name)

        if pump == 'VICI M50':
            self.control_box_sizer.Show(self.m50_settings_sizer, recursive=True)

    def _on_units(self, evt):
        """Called when the units are changed in the GUI."""
        vol_unit = self.vol_unit_ctrl.GetStringSelection()
        t_unit = self.time_unit_ctrl.GetStringSelection()

        old_units = self.flow_units_lbl.GetLabel()

        self.flow_units_lbl.SetLabel('{}/{}'.format(vol_unit, t_unit))
        self.vol_units_lbl.SetLabel(vol_unit)

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
                if mode == 'Fixed volume':
                    self._flow_timer.Start(1000)
                    cmd = self.direction_ctrl.GetStringSelection().lower()
                    self._send_cmd(cmd)
                    self._set_status(cmd.capitalize())
                else:
                    self._send_cmd('start_flow')
                    self._set_status('Flowing')
                    self.fr_button.Show()

                self.run_button.SetLabel('Stop')

            else:
                logger.info('Stopping pump %s flow', self.name)
                self._send_cmd('stop')

                self.run_button.SetLabel('Start')
                self.fr_button.Hide()
                if self._flow_timer.IsRunning():
                    self._flow_timer.Stop()

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

        logger.info('Connected to pump %s', self.name)
        self.connected = True
        self.connect_button.SetLabel('Reconnect')
        self._send_cmd('connect')

        start_time = time.time()
        while len(self.answer_q) == 0 and time.time()-start_time < 5:
            time.sleep(0.01)

        if len(self.answer_q) > 0:
            connected = self.answer_q.popleft()
        else:
            connected = False

        if connected:
            self._set_status('Connected')
        else:
            self._set_status('Connection Failed')

        return

    def _set_status(self, status):
        """
        Changes the status in the GUI.

        :param str status: The status to display.
        """
        logger.debug('Setting pump %s status to %s', self.name, status)
        self.status.SetLabel(status)

    def _set_flowrate(self):
        """
        Sets the flowrate for the pump.

        :returns: ``True`` if the flow rate is set successfully, ``False`` otherwise.
        :rtype: bool
        """
        self._send_cmd('set_units')
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

        return success

    def _on_flow_timer(self, evt):
        """
        Called every second when the pump is moving in fixed volume mode.
        It checks the pump status, and if it is done moving it updates the GUI
        status.
        """
        self._send_cmd('is_moving')
        start_time = time.time()
        while len(self.answer_q) == 0 and time.time()-start_time < 0.5:
            time.sleep(0.01)

        if len(self.answer_q) > 0:
            is_moving = self.answer_q.popleft()
        else:
            is_moving = True

        if not is_moving:
            self.run_button.SetLabel('Start')
            self.fr_button.Hide()
            self._flow_timer.Stop()
            self._set_status('Done')

    def _send_cmd(self, cmd):
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
            self.pump_cmd_q.append(('start_flow', (self.name,), {}))
        elif cmd == 'stop':
            self.pump_cmd_q.append(('stop', (self.name,), {}))
        elif cmd == 'dispense':
            vol = float(self.volume_ctrl.GetValue())
            self.pump_cmd_q.append(('dispense', (self.name, vol), {}))
        elif cmd == 'aspirate':
            vol = float(self.volume_ctrl.GetValue())
            self.pump_cmd_q.append(('aspirate', (self.name, vol), {}))
        elif cmd == 'set_flow_rate':
            direction = self.direction_ctrl.GetStringSelection().lower()
            if direction == 'dispense':
                mult = 1
            else:
                mult = -1
            fr = mult*float(self.flow_rate_ctrl.GetValue())
            self.pump_cmd_q.append(('set_flow_rate', (self.name, fr), {}))
        elif cmd == 'set_units':
            units = self.flow_units_lbl.GetLabel()
            self.pump_cmd_q.append(('set_units', (self.name, units), {}))
        elif cmd == 'connect':
            com = self.com_ctrl.GetStringSelection()
            pump = self.type_ctrl.GetStringSelection().replace(' ', '_')

            args = (com, self.name, pump)

            if pump == 'VICI_M50':
                fc = float(self.m50_fcal.GetValue())
                bc = float(self.m50_bcal.GetValue())
                kwargs = {'flow_cal': fc, 'backlash_cal':bc}
            else:
                kwargs = {}

            self.pump_cmd_q.append(('connect', args, kwargs))


class PumpFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, *args, **kwargs):
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

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._get_ports()

        self.pumps =[]

        top_sizer = self._create_layout()

        self.SetSizer(top_sizer)

        self.Fit()
        self.Raise()

        # self._initpumps()

    def _create_layout(self):
        """Creates the layout"""
        pump_panel = PumpPanel(self, wx.ID_ANY, 'stand_in', self.ports,
            self.pump_cmd_q, self.pump_answer_q, self.pump_con.known_pumps,
            'stand_in')

        self.pump_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.pump_sizer.Add(pump_panel, flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN)

        # self.pump_sizer.Hide(pump_panel, recursive=True)

        button_panel = wx.Panel(self)

        add_pump = wx.Button(button_panel, label='Add pump')
        add_pump.Bind(wx.EVT_BUTTON, self._on_addpump)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_pump)

        button_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        button_panel_sizer.Add(wx.StaticLine(button_panel), flag=wx.EXPAND|wx.TOP|wx.BOTTOM, border=2)
        button_panel_sizer.Add(button_sizer, flag=wx.ALIGN_RIGHT|wx.BOTTOM|wx.RIGHT, border=2)

        button_panel.SetSizer(button_panel_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.pump_sizer, flag=wx.EXPAND)
        top_sizer.Add(button_panel, flag=wx.EXPAND)

        return top_sizer

    def _initpumps(self):
        """
        This is a convenience function for initalizing pumps on startup, if you
        already know what pumps you want to add. You can comment it out in
        the ``__init__`` if you want to not load any pumps on startup.

        If you want to add pumps here, add them to the ``setup_pumps`` list.
        Each entry should be an iterable with the following parameters: name,
        pump type, comport, arg list, and kwarg dict in that order. How the
        arg list and kwarg dict are handled are defined in the
        :py:func:`PumpPanel._initpump` function, and depends on the pump type.
        """
        if not self.pumps:
            self.pump_sizer.Remove(0)

        setup_pumps = [('2', 'VICI M50', 'COM5', ['626.2', '9.278'], {}),
                    ('3', 'VICI M50', 'COM6', ['627.32', '11.826'], {})
                    ]

        logger.info('Initializing %s pumps on startup', str(len(setup_pumps)))

        for pump in setup_pumps:
            new_pump = PumpPanel(self, wx.ID_ANY, pump[0], self.ports, self.pump_cmd_q,
                self.pump_answer_q, self.pump_con.known_pumps, pump[0], pump[1],
                pump[2], pump[3], pump[4])

            self.pump_sizer.Add(new_pump)
            self.pumps.append(new_pump)

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

            new_pump = PumpPanel(self, wx.ID_ANY, name, self.ports, self.pump_cmd_q,
                self.pump_con.known_pumps, name)
            logger.info('Added new pump %s to the pump control panel.', name)
            self.pump_sizer.Add(new_pump)
            self.pumps.append(new_pump)

            self.Layout()
            self.Fit()

        return

    def _get_ports(self):
        """
        Gets a list of active comports.

        .. note:: This doesn't update after the program is opened, so you need
            to start the program after all pumps are connected to the computer.
        """
        port_info = list_ports.comports()
        self.ports = [port.device for port in port_info]

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
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    # my_pump = M50Pump('COM6', '2', 626.2, 9.278)

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

    app = wx.App()
    logger.debug('Setting up wx app')
    frame = PumpFrame(None, title='Pump Control')
    frame.Show()
    app.MainLoop()


