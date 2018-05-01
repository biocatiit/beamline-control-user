# coding: utf-8
#
#    Project: BioCAT beamline control software (BioCON)
#             https://github.com/silx-kit/fabio
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

import traceback
import threading
import time
import collections
from collections import OrderedDict, deque
import queue

import serial
import wx

print_lock = threading.RLock()

class SerialComm():
    """This class impliments a generic serial communication setup. The goal is
    to provide a lightweight wrapper around a pyserial Serial device to make sure
    ports are properly opened and closed whenever used.
    """
    def __init__(self, port=None, baudrate=9600, bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=None,
        xonxoff=False, rtscts=False, write_timeout=None, dsrdtr=False,
        inter_byte_timeout=None, exclusive=None):
        """Parameters are all of those accepted by a
        `pyserial.Serial <https://pyserial.readthedocs.io/en/latest/pyserial_api.html#serial.Serial>`_
        device, defaults are set to those default values.
        """
        self.ser = None

        try:
            self.ser = serial.Serial(port, baudrate, bytesize, parity, stopbits, timeout,
                xonxoff, rtscts, write_timeout, dsrdtr, inter_byte_timeout, exclusive)
        except ValueError as err:
            with print_lock:
                traceback.print_tb(err.__traceback__)
        except serial.SerialException as err:
            with print_lock:
                traceback.print_tb(err.__traceback__)
        finally:
            if self.ser is not None:
                self.ser.close()

    def __repr__(self):
        return self.ser

    def __str__(self):
        return print(self.ser)

    def read(self, size=1):
        """This wraps the Serial.read() function for reading in a specified
        number of bytes. It automatically decodes the return value.

        :param size: Number of bytes to read.
        :type size: int

        :returns: The ascii (decoded) value of the ``Serial.read()``
        :rtype: str
        """
        with self.ser as s:
            ret = s.read(size)

        return ret.decode()

    def read_all(self):
        """This wraps the Serial.read() function, and returns all of the
        waiting bytes.

        :returns: The ascii (decoded) value of the ``Serial.read()``
        :rtype: str
        """
        with self.ser as s:
            ret = s.read(s.in_waiting())

        return ret.decode()

    def write(self, data, get_response=False, term_char='>'):
        """This warps the Serial.write() function. It encodes the input
        data if necessary. It can return any expected response from the
        controller.

        :param data: Data to be written to the serial device.
        :type data: str, bytes

        :param term_char: The terminal character expected in a response
        :type term_char: str

        :returns: The requested response, or an empty string
        :rtype: str
        """
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
        except ValueError as err:
            with print_lock:
                traceback.print_tb(err.__traceback__)

        return out

class MForceSerialComm(SerialComm):
    """This class subclases ``SerialComm`` to handle MForce specific
    errors.
    """

    def write(self, data, get_response=True, term_char='>'):
        """This warps the Serial.write() function. It encodes the input
        data if necessary. It can return any expected response from the
        controller.

        :param data: Data to be written to the serial device.
        :type data: str, bytes

        :param term_char: The terminal character expected in a response
        :type term_char: str

        :returns: The requested response, or an empty string
        :rtype: str
        """
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
                            # print(out)

                        if out.strip().endswith('?'):
                            print('sending error command')
                            s.write('PR ER\r\n'.encode())
                            out = ''

                        time.sleep(.001)
        except ValueError as err:
            with print_lock:
                traceback.print_tb(err.__traceback__)

        return out

class Pump():
    """
    This class contains the settings and communication for a generic pump.
    It is intended to be subclassed by other pump classes, which contain
    specific information for communicating with a given pump.
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
        """Sets and returns the pump flow rate in units specified by ``Pump.units``.
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
        """Sets and returns the pump flow rate units. This can be set to:
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
        else:
            print('Units must be one of: {}'.format(', '.join(['nL/s', 'nL/min', 'uL/s', 'uL/min', 'mL/s', 'mL/min'])))

    def send_cmd(self, cmd, get_response=True):
        """Sends a command to the pump.

        :param cmd: The command to send to the pump.

        :param get_response: Whether the program should get a response from the pump
        :type get_response: bool
        """
        pass #Should be implimented in each subclass


    def is_moving(self):
        """Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        pass #Should be implimented in each subclass

    def start_flow(self):
        """Starts a continuous flow at the flow rate specified by the
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

class M50Pump(Pump):
    """This class contains information for initializing and communicating with
    a VICI M50 Pump using an MForce Controller.

    .. todo::
        This needs to have a backlash correction for dispensing/aspirating
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

        self.pump_comm = MForceSerialComm(device)

        #Make sure parameters are set right
        self.send_cmd('EM 0') #Echo mode to full duplex
        self.send_cmd('MS 256') #Microstepping to 256, MForce default
        self.send_cmd('VI 1000') #Initial velocity to 1000, MForce default
        self.send_cmd('A 1000000') #Acceleration to 1000000, MForce default
        self.send_cmd('D 1000000') #Deceleration to 1000000, MForce default
        self.send_cmd('HC 5') #Hold current to 5%, MForce default
        self.send_cmd('RC 25') #Run current to 25%, MForce default is 25%
        # # self.send_cmd('S') #Saves current settings in non-volatile memory

        self._is_flowing = False
        self._is_dispensing = False

        self._units = 'uL/min'
        self._flow_rate = 0

        self._flow_cal = flow_cal
        self._backlash_cal = backlash_cal
        self.gear_ratio = 14.915 #Gear ratio provided by manufacturer, for M50 pumps

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
        if self.units.split('/')[0] == 'mL':
            rate = rate*1000.
        elif self.units.split('/')[0] == 'nL':
            rate = rate/1000.

        if self.units.split('/')[1] == 'min':
            rate = rate/60.

        #Maximum continuous flow rate is 25 mL/min
        if rate>25000/60.:
            rate = 25000/60.
        elif rate<-25000/60.:
            rate = -25000/60.

        #Minimum flow rate is 1 uL/min
        if abs(rate) < 1/60. and rate != 0:
            if rate>0:
                rate = 1/60.
            else:
                rate = -1/60.


        self._flow_rate = int(round(rate*self.cal))

        if self._is_flowing:
            self.send_cmd("SL {}".format(self._flow_rate))
        elif self._is_dispensing:
            self.send_cmd("VM {}".format(self._flow_rate))


    def send_cmd(self, cmd, get_response=True):
        """Sends a command to the pump.

        :param cmd: The command to send to the pump.
        :type cmd: str, bytes

        :param get_response: Whether the program should get a response from the pump
        :type get_response: bool
        """
        with print_lock:
            print("Sending cmd: {!r} to {}".format(cmd, self.name))
        ret = self.pump_comm.write(cmd, get_response)

        if get_response:
            with print_lock:
                print('Returned: {!r}'.format(ret))
        return ret


    def is_moving(self):
        status = self.send_cmd("PR MV")

        status = status.split('\r\n')[-2][-1]
        status = bool(int(status))

        return status

    def start_flow(self):
        self.send_cmd("SL {}".format(self._flow_rate))
        self._is_flowing = True

    def dispense(self, vol, units='uL'):
        if units == 'mL':
            vol = vol*1000.
        elif units == 'nL':
            vol = vol/1000.

        vol =int(round(vol*self.cal))

        self.send_cmd("VM {}".format(self._flow_rate))
        self.send_cmd("MR {}".format(vol))
        self._is_dispensing = True

    def aspirate(self, vol, units='uL'):
        self.dispense(-1*vol, units)

    def stop(self):
        self.send_cmd("SL 0")
        self.send_cmd("\x1B")
        self.is_flowing = False
        self._is_dispensing = False

class PumpCommThread(threading.Thread):
    """
    This class creates a control thread for pumps attached to the system.
    """

    def __init__(self, command_queue, abort_event):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``_known_pumps``.

        :param collections.deque command_queue: The queue used to pass commands to
            the thread.

        :param threading.Event abort_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        threading.Thread.__init__(self)

        self.command_queue = command_queue
        self._abort_event = abort_event
        self._stop_event = threading.Event()

        self._commands = {'connect'     : self._connect_pump,
                        'set_flow_rate' : self._set_flow_rate,
                        'set_units'     : self._set_units,
                        'start_flow'    : self._start_flow,
                        'stop'          : self._stop,
                        'aspirate'      : self._aspirate,
                        'dispense'      : self._dispense,
                        'is_moving'     : self._is_moving,
                        'send_cmd'      : self._send_cmd,
                        }

        self._connected_pumps = OrderedDict()

        self._known_pumps = {'VICI_M50' : M50Pump,
                            }

    def run(self):
        """
        Custom run method for the thread.
        """
        while True:
            if len(self.command_queue) > 0:
                command, args, kwargs = self.command_queue.popleft()
            else:
                command = None

            if self._abort_event.is_set():
                self._abort()
                command = None

            if self._stop_event.is_set():
                break

            if command is not None:
                with print_lock:
                    print(command)
                    print(args)
                    print(kwargs)
                self._commands[command](*args, **kwargs)

        self._abort()

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

        :param pump_type: A pump type in the ``_known_pumps`` dictionary.
        :type pump_type: str

        :param \*\*kwargs: This function accepts arbitrary keyword args that are passed
            directly to the :py:class:`Pump` subclass that is called. For example,
            for an :py:class:`M50Pump` you could pass ``flow_cal`` and ``backlash``.
        """
        new_pump = self._known_pumps[pump_type](device, name, **kwargs)

        self._connected_pumps[name] = new_pump

    def _set_flow_rate(self, name, flow_rate):
        """
        This method sets the flow rate for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float flow_rate: The flow rate for the pump.
        """
        pump = self._connected_pumps[name]
        pump.flow_rate = flow_rate

        with print_lock:
            print(pump)
            print(pump.flow_rate)

    def _set_units(self, name, units):
        """
        This method sets the units for the flow rate for a pump. This can be set to:
        nL/s, nL/min, uL/s, uL/min, mL/s, mL/min. Changing units keeps the
        flow rate constant, i.e. if the flow rate was set to 100 uL/min, and
        the units are changed to mL/min, the flow rate is set to 0.1 mL/min.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float units: The flow rate for the pump.
        """
        pump = self._connected_pumps[name]
        pump.units = units

    def _start_flow(self, name):
        """
        This method starts continuous flow for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        pump = self._connected_pumps[name]
        pump.start_flow()

        with print_lock:
            print(pump)
            print(pump.is_moving())

    def _stop(self, name):
        """
        This method stops all flow (continuous or finite) for a pump.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.
        """
        pump = self._connected_pumps[name]
        pump.stop()

    def _aspirate(self, name, vol, units='uL'):
        """
        This method aspirates a fixed volume.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float vol: The volume to aspriate.

        :param str units: The units of the volume, can be nL, uL, or mL. Defaults to uL.
        """
        pump = self._connected_pumps[name]
        pump.aspirate(vol, units)

    def _dispense(self, name, vol, units='uL'):
        """
        This method dispenses a fixed volume.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param float vol: The volume to aspriate.

        :param str units: The units of the volume, can be nL, uL, or mL. Defaults to uL.
        """
        pump = self._connected_pumps[name]
        pump.dispense(vol, units)

    def _is_moving(self, name, return_queue):
        """
        This method returns where or not the pump is moving.

        :param str name: The unique identifier for a pump that was used in the
            :py:func:`_connect_pump` method.

        :param return_queue: The return queue to put the response in.
        :type return_queue: queue.Queue

        :rtype: bool
        """
        pump = self._connected_pumps[name]
        is_moving = pump.is_moving()

        return_queue.put_nowait(is_moving)

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
        pump = self._connected_pumps[name]
        pump.send_cmd(cmd, get_response)

    def _abort(self):
        """
        Clears the ``command_queue`` and aborts all current pump motions.
        """

        self.command_queue.clear()

        for name, pump in self._connected_pumps.items():
            pump.stop()

        self._abort_event.clear()

    def stop(self):
        self._stop_event.set()


if __name__ == '__main__':
    # my_pump = M50Pump('COM6', 'pump2', 626.2, 9.278)
    pmp_cmd_q = deque()
    abort_event = threading.Event()
    my_pumpcon = PumpCommThread(pmp_cmd_q, abort_event)

    my_pumpcon.start()


    init_cmd = ('connect', ('COM6', 'pump2', 'VICI_M50'),
        {'flow_cal': 626.2, 'backlash_cal': 9.278})
    pmp_cmd_q.append(init_cmd)

    fr_cmd = ('set_flow_rate', ('pump2', 2000), {})
    pmp_cmd_q.append(fr_cmd)

    start_cmd = ('start_flow', ('pump2',), {})
    pmp_cmd_q.append(start_cmd)
    
    # for i in range(10):
    #     with print_lock:
    #         print('sleeping {}'.format(i))
    #     time.sleep(1)

    stop_cmd = ('stop', ('pump2',), {})
    # pmp_cmd_q.append(stop_cmd)

    # time.sleep(5)

