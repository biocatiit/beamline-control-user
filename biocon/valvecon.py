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

import threading
import time
from collections import OrderedDict, deque
import logging
import sys
import ctypes
import copy

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import serial
import serial.tools.list_ports as list_ports
from six import string_types

import utils

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

    def write(self, data, get_response=False, send_term_char = '\r\n',
        term_char='>', timeout=0.25):
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
                    start_time = time.time()
                    while not out.endswith(term_char) and time.time()-start_time <timeout:
                        if s.in_waiting > 0:
                            ret = s.read(s.in_waiting)
                            out += ret.decode('ascii')

                        time.sleep(.001)
        except ValueError:
            logger.exception("Failed to write '%s' to serial device on port %s", data, self.ser.port)

        logger.debug("Recived '%s' after writing to serial device on port %s", out, self.ser.port)

        return out


class Valve(object):
    """
    This class contains the settings and communication for a generic valve.
    It is intended to be subclassed by other valve classes, which contain
    specific information for communicating with a given pump. A valve object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`ValveCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, device, name, comm_lock=None):
        """
        :param str device: The device comport

        :param str name: A unique identifier for the device
        """

        self.device = device
        self.name = name

        if comm_lock is None:
            self.comm_lock = threading.Lock()
        else:
            self.comm_lock = comm_lock

        self._position = None

    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    def __str__(self):
        return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    def get_status(self):
        pass

    def get_error(self):
        pass

    def get_position(self):
        pass

    def set_position(self, position):
        pass

    def send_command(self):
        pass

    def stop(self):
        pass


class RheodyneValve(Valve):
    """
    This class contains information for initializing and communicating with
    a Elveflow Bronkhurst FLow Sensor (BFS), communicating via the Elveflow SDK.
    Below is an example that starts communication and prints the flow rate. ::

        >>> my_bfs = BFS("ASRL8::INSTR".encode('ascii'), 'BFS1')
        >>> print(my_bfs.flow_rate)
    """

    def __init__(self, device, name, positions, comm_lock=None):
        """
        This makes the initial serial connection, and then sets the MForce
        controller parameters to the correct values.

        :param str device: The device comport as sent to pyserial

        :param str name: A unique identifier for the pump

        :param float bfs_filter: Smoothing factor for measurement. 1 = minimum
            filter, 0.00001 = maximum filter. Defaults to 1
        """
        Valve.__init__(self, device, name, comm_lock=comm_lock)

        logstr = ("Initializing valve {} on port {}".format(self.name,
            self.device))
        logger.info(logstr)

        self.comm_lock.acquire()
        self.valve_comm = SerialComm(device, 19200)
        self.comm_lock.release()

        self.send_command('M', False) #Homes valve

        self.err_msgs = {
            '99'   : 'Valve failure (valve cannot be homed)',
            '88'    : 'Non-volatile memory error',
            '77'    : 'Valve configuration error or command mode error',
            '66'    : 'Valve positioning error',
            '55'    : 'Data integrity error',
            '44'    : 'Data CRC error',
            }

        self._positions = int(positions)

        # logger.exception('Initialization error: {}'.format(error))

    def get_status(self):
        status, success = self.send_command('S')
        return status

    def get_error(self):
        error, success = self.send_command('E')

        if success:
            error = self.err_msgs[error]
        else:
            error = None

        return error

    def get_position(self):
        status, success = self.send_command('S')

        try:
            int(status)
        except Exception:
            success = False

        if success:
            if int(status) < 12:
                logger.debug("Valve %s position %s", self.name, status)
                position = status
            else:
                err = self.err_msgs[status]
                logger.error('Valve %s could not get position, valve error: %s',
                    self.name, err)
                position = None
        else:
            position = None

        return position

    def set_position(self, position):
        position = int(position)

        if position > self._positions:
            logger.error('Cannot set valve to position %i, maximum position is %i',
                position, self._positions)
            success = False
        elif position < 1:
            logger.error('Cannot set valve to position %i, minimum position is 1',
                position)
            success = False

        else:
            if position < 10:
                position = '0{}'.format(position)
            elif position == 10:
                position = '0A'
            elif position == 11:
                position = '0B'
            elif position == 12:
                position = '0C'

            ret, success = self.send_command('P{}'.format(position))

        return success

    def send_command(self, cmd, get_response=True):
        self.comm_lock.acquire()
        ret = self.valve_comm.write(cmd, get_response, send_term_char = '\r', term_char='\r')
        self.comm_lock.release()

        if '*' in ret:
            success = False
        else:
            success = True
            try:
                ret = str(int(ret, 16))
            except Exception:
                pass

        return ret, success


class CheminertValve(Valve):
    """
    A VICI cheminert valve with universal actuator and serial control.
    """

    def __init__(self, device, name, positions, comm_lock=None):
        """
        """
        Valve.__init__(self, device, name, comm_lock=comm_lock)

        logstr = ("Initializing valve {} on port {}".format(self.name,
            self.device))
        logger.info(logstr)

        self.comm_lock.acquire()
        self.valve_comm = SerialComm(device, 9600)
        self.comm_lock.release()

        # self.send_command('IFM1', False) #Sets the response mode to basic
        # self.send_command('SMA', False) #Sets the rotation mode to auto (shortest)
        # self.send_command('AM3', False) #Sets mode to multiposition valve
        # self.send_command('HM', False) #Homes valve

        self._positions = int(positions)


    def get_position(self):
        position = self.send_command('CP')[0]
        position = position.strip().lstrip('CP')

        try:
            position = '{}'.format(int(position))
            success = True
        except Exception:
            success = False

        if success:
            logger.debug("Valve %s position %s", self.name, position)
        else:
            logger.error('Valve %s could not get position', self.name)
            position = None

        return position

    def set_position(self, position):
        position = int(position)

        if position > self._positions:
            logger.error('Cannot set valve to position %i, maximum position is %i',
                position, self._positions)
            success = False
        elif position < 1:
            logger.error('Cannot set valve to position %i, minimum position is 1',
                position)
            success = False

        else:
            position = '{}'.format(position)

            ret, success = self.send_command('GO{}'.format(position))

        return success

    def send_command(self, cmd, get_response=True):
        self.comm_lock.acquire()
        ret = self.valve_comm.write(cmd, get_response, send_term_char = '\r', term_char='\r')
        self.comm_lock.release()

        if '' != ret:
            success = True
        else:
            success = True

        return ret, success

class SoftValve(Valve):
    """
    This class contains the settings and communication for a generic valve.
    It is intended to be subclassed by other valve classes, which contain
    specific information for communicating with a given pump. A valve object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`ValveCommThread`
    or it can be used directly from the command line. The :py:class:`M5Pump`
    documentation contains an example.
    """

    def __init__(self, device, name, positions, comm_lock=None):
        """
        :param str device: The device comport

        :param str name: A unique identifier for the device
        """

        Valve.__init__(self, device, name, comm_lock=comm_lock)

        self._position = 1
        self._positions = int(positions)

    def get_status(self):
        return ''

    def get_error(self):
        return ''

    def get_position(self):
        return self._position

    def set_position(self, position):
        if position > self._positions:
            logger.error('Cannot set valve to position %i, maximum position is %i',
                position, self._positions)
            success = False
        elif position < 1:
            logger.error('Cannot set valve to position %i, minimum position is 1',
                position)
            success = False

        else:
            self._position = int(position)
            success = True

        return success

class ValveCommThread(threading.Thread):
    """
    This class creates a control thread for flow meters attached to the system.
    This thread is designed for using a GUI application. For command line
    use, most people will find working directly with a flow meter object much
    more transparent. Below you'll find an example that initializes a
    :py:class:`BFS` and measures the flow. ::

        import collections
        import threading

        valve_cmd_q = deque()
        valve_return_q = deque()
        abort_event = threading.Event()
        my_valvecon = ValveCommThread(valve_cmd_q, valve_return_q, abort_event, 'ValveCon')
        my_valvecon.start()
        init_cmd = ('connect', ('/dev/cu.usbserial-AC01UZ8O', 'r6p7_1', 'Rheodyne'), {'positions' : 6})
        get_pos_cmd = ('get_position', ('r6p7_1',), {})
        set_pos_cmd = ('set_position', ('r6p7_1', 5), {})
        status_cmd = ('get_status', ('r6p7_1',), {})

        valve_cmd_q.append(init_cmd)
        time.sleep(0.1)
        valve_cmd_q.append(get_pos_cmd)
        valve_cmd_q.append(set_pos_cmd)
        valve_cmd_q.append(status_cmd)
        time.sleep(5)
        my_valvecon.stop()
    """

    def __init__(self, command_queue, return_queue, abort_event, name=None):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_fms``.

        :param collections.deque command_queue: The queue used to pass commands
            to the thread.

        :param collections.deque return_queue: The queue used to return data
            from the thread.

        :param threading.Event abort_event: An event that is set when the thread
            needs to abort, and otherwise is not set.
        """
        threading.Thread.__init__(self, name=name)
        self.daemon = True

        logger.info("Starting valve control thread: %s", self.name)

        self.command_queue = command_queue
        self.return_queue = return_queue
        self._abort_event = abort_event
        self._stop_event = threading.Event()

        self._commands = {'connect'         : self._connect_valve,
                        'get_position'      : self._get_position,
                        'set_position'      : self._set_position,
                        'get_status'        : self._get_status,
                        'add_valve'         : self._add_valve,
                        'disconnect'        : self._disconnect,
                        'add_comlocks'      : self._add_comlocks,
                        'connect_remote'    : self._connect_valve_remote,
                        'get_position_multi': self._get_position_multiple,
                        'set_position_multi': self._set_position_multiple,
                        }

        self._connected_valves = OrderedDict()

        self.comm_locks = {}

        self.known_valves = {'Rheodyne' : RheodyneValve,
            'Soft'  : SoftValve,
            'Cheminert' : CheminertValve,
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
                    msg = ("Valve control thread failed to run command '%s' "
                        "with args: %s and kwargs: %s " %(command,
                        ', '.join(['{}'.format(a) for a in args]),
                        ', '.join(['{}:{}'.format(kw, item) for kw, item in kwargs.items()])))
                    logger.exception(msg)

                    if command == 'connect' or command == 'disconnect':
                        self.return_queue.append((command, False))

            else:
                time.sleep(0.01)

        if self._stop_event.is_set():
            self._stop_event.clear()
        else:
            self._abort()

        for valve in self._connected_valves.values():
            valve.stop()

        logger.info("Quitting valve control thread: %s", self.name)

    def _connect_valve(self, device, name, valve_type, **kwargs):
        """
        This method connects to a flow meter by creating a new :py:class:`FlowMeter`
        subclass object (e.g. a new :py:class:`BFS` object). This pump is saved
        in the thread and can be called later to do stuff. All pumps must be
        connected before they can be used.

        :param str device: The device comport

        :param str name: A unique identifier for the pump

        :param str pump_type: A pump type in the ``known_fms`` dictionary.

        :param kwargs: This function accepts arbitrary keyword args that
            are passed directly to the :py:class:`FlowMeter` subclass that is
            called. For example, for a :py:class:`BFS` you could pass ``bfs_filter``.
        """
        logger.info("Connecting valve %s", name)
        new_valve = self.known_valves[valve_type](device, name, **kwargs)
        self._connected_valves[name] = new_valve
        logger.debug("Valve %s connected", name)

        self.return_queue.append(('connected', name, True))

    def _connect_valve_remote(self, device, name, valve_type, **kwargs):
        """
        This method connects to a flow meter by creating a new :py:class:`FlowMeter`
        subclass object (e.g. a new :py:class:`BFS` object). This pump is saved
        in the thread and can be called later to do stuff. All pumps must be
        connected before they can be used.

        :param str device: The device comport

        :param str name: A unique identifier for the pump

        :param str pump_type: A pump type in the ``known_fms`` dictionary.

        :param kwargs: This function accepts arbitrary keyword args that
            are passed directly to the :py:class:`FlowMeter` subclass that is
            called. For example, for a :py:class:`BFS` you could pass ``bfs_filter``.
        """
        logger.info("Connecting valve %s", name)
        if device in self.comm_locks:
            kwargs['comm_lock'] = self.comm_locks[device]

        new_valve = self.known_valves[valve_type](device, name, **kwargs)
        self._connected_valves[name] = new_valve
        self.return_queue.append(('connected', name, True))

        logger.debug("Valve %s connected", name)

    def _add_valve(self, valve, name, **kwargs):
        logger.info('Adding valve %s', name)
        self._connected_valves[name] = valve
        self.return_queue.append((name, 'add', True))
        logger.debug('Valve %s added', name)

    def _disconnect(self, name):
        logger.info("Disconnecting valve %s", name)
        valve = self._connected_valves[name]
        valve.stop()
        del self._connected_valves[name]
        logger.debug("Valve %s disconnected", name)

        self.return_queue.append(('disconnected', name, True))

    def _get_position(self, name):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.
        """
        logger.debug("Getting valve %s position", name)
        valve = self._connected_valves[name]
        position = valve.get_position()
        logger.debug("Valve %s position: %s", name, position)

        self.return_queue.append(('position', name, position))

    def _get_position_multiple(self, names):
        logger.debug("Getting multiple valve positions")
        positions = []
        for name in names:
            valve = self._connected_valves[name]
            position = valve.get_position()

            positions.append(position)

        self.return_queue.append(('multi_positions', names, positions))

    def _get_status(self, name):
        """
        This method gets the flow rate measured by a flow meter.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.

        .. note:: Only the BFS flow meters can read density as well as flow rate.
        """
        logger.debug("Getting valve %s status", name)
        valve = self._connected_valves[name]
        status = valve.get_status()
        logger.debug("Valve %s status: %f", name, status)

        self.return_queue.append(('status', name, status))

    def _set_position(self, name, position):
        """
        This method sets the units for the flow rate for a flow meter. This
        can be set to: nL/s, nL/min, uL/s, uL/min, mL/s, mL/min.

        :param str name: The unique identifier for a flow meter that was used
            in the :py:func:`_connect_fm` method.

        :param str units: The units for the fm.
        """
        logger.debug("Setting valve %s position", name)
        valve = self._connected_valves[name]
        success = valve.set_position(position)
        if success:
            logger.info("Valve %s position set to %i", name, position)
        else:
            logger.info("Failed setting valve %s position to %i", name, position)

        self.return_queue.append(('set_position', name, success))

    def _set_position_multiple(self, names, positions):
        logger.debug('Setting multiple valve positions')
        success = []
        for i, name in enumerate(names):
            logger.debug("Setting valve %s position", name)
            valve = self._connected_valves[name]
            t_success = valve.set_position(positions[i])
            if t_success:
                logger.info("Valve %s position set to %i", name, positions[i])
            else:
                logger.info("Failed setting valve %s position to %i", name, positions[i])
            success.append(t_success)

        if all(success):
            logger.info('Set all valve positions successfully')

        self.return_queue.append(('set_position_multi', names, success))


    def _add_comlocks(self, comm_locks):
        self.comm_locks.update(comm_locks)

    def _abort(self):
        """
        Clears the ``command_queue`` and the ``return_queue``.
        """
        logger.info("Aborting valve control thread %s current and future commands", self.name)
        self.command_queue.clear()
        self.return_queue.clear()

        self._abort_event.clear()
        logger.debug("Valve control thread %s aborted", self.name)

    def stop(self):
        """Stops the thread cleanly."""
        logger.info("Starting to clean up and shut down valve control thread: %s", self.name)
        self._stop_event.set()

class ValvePanel(wx.Panel):
    """
    This flow meter panel supports standard settings, including connection settings,
    for a flow meter. It is meant to be embedded in a larger application and can
    be instanced several times, once for each flow meter. It communciates
    with the flow meters using the :py:class:`FlowMeterCommThread`. Currently
    it only supports the :py:class:`BFS`, but it should be easy to extend for
    other flow meters. The only things that should have to be changed are
    are adding in flow meter-specific readouts, modeled after how the
    ``bfs_pump_sizer`` is constructed in the :py:func:`_create_layout` function,
    and then add in type switching in the :py:func:`_on_type` function.
    """
    def __init__(self, parent, panel_id, panel_name, all_comports, valve_cmd_q,
        valve_return_q, known_valves, valve_name, valve_type=None, comport=None, valve_args=[],
        valve_kwargs={}, comm_lock=None):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known pumps ``known_valves``.

        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        :param str panel_name: Name for the panel

        :param list all_comports: A list containing all comports that the flow meter
            could be connected to.

        :param collections.deque valve_cmd_q: The ``valve_cmd_q`` that was passed to
            the :py:class:`FlowMeterCommThread`.

        :param collections.deque valve_return_q: The ``valve_return_q`` that was passed to
            the :py:class:`FlowMeterCommThread`.

        :param list known_valves: The list of known flow meter types, obtained from
            the :py:class:`FlowMeterCommThread`.

        :param str valve_name: An identifier for the flow meter, displayed in the
            flow meter panel.

        :param str valve_type: One of the ``known_valves``, corresponding to the flow
            meter connected to this panel. Only required if you are connecting
            the flow meter when the panel is first set up (rather than manually
            later).

        :param str comport: The comport the flow meter is connected to. Only required
            if you are connecting the flow meter when the panel is first set up (rather
            than manually later).

        :param list valve_args: Flow meter specific arguments for initialization.
            Only required if you are connecting the flow meter when the panel is first
            set up (rather than manually later).

        :param dict valve_kwargs: Flow meter specific keyword arguments for initialization.
            Only required if you are connecting the flow meter when the panel is first
            set up (rather than manually later).

        """

        wx.Panel.__init__(self, parent, panel_id, name=panel_name)
        logger.debug('Initializing ValvePanel for flow meter %s', valve_name)

        self.name = valve_name
        self.valve_cmd_q = valve_cmd_q
        self.all_comports = all_comports
        self.known_valves = known_valves
        self.answer_q = valve_return_q
        self.connected = False
        self.position = None

        self.comm_lock = comm_lock

        self.top_sizer = self._create_layout()

        self._position_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_position_timer, self._position_timer)

        self.SetSizer(self.top_sizer)

        self._initvalve(valve_type, comport, valve_args, valve_kwargs)


    def _create_layout(self):
        """Creates the layout for the panel."""
        self.status = wx.StaticText(self, label='Not connected')

        status_grid = wx.FlexGridSizer(rows=2, cols=2, vgap=2, hgap=2)
        status_grid.AddGrowableCol(1)
        status_grid.Add(wx.StaticText(self, label='Valve name:'))
        status_grid.Add(wx.StaticText(self, label=self.name), 1, wx.EXPAND)
        status_grid.Add(wx.StaticText(self, label='Status: '))
        status_grid.Add(self.status, 1, wx.EXPAND)

        status_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Info'),
            wx.VERTICAL)
        status_sizer.Add(status_grid, 1, wx.EXPAND)

        self.type_ctrl = wx.Choice(self,
            choices=[item.replace('_', ' ') for item in self.known_valves.keys()],
            style=wx.CB_SORT)
        self.type_ctrl.SetSelection(0)
        self.com_ctrl = wx.Choice(self, choices=self.all_comports, style=wx.CB_SORT)
        self.positions_ctrl = wx.TextCtrl(self, size=(60, -1))

        self.type_ctrl.Bind(wx.EVT_CHOICE, self._on_type)

        gen_settings_sizer = wx.FlexGridSizer(rows=4, cols=2, vgap=2, hgap=2)
        gen_settings_sizer.AddGrowableCol(1)
        gen_settings_sizer.Add(wx.StaticText(self, label='Valve type:'))
        gen_settings_sizer.Add(self.type_ctrl, 1, wx.EXPAND)
        gen_settings_sizer.Add(wx.StaticText(self, label='COM port:'))
        gen_settings_sizer.Add(self.com_ctrl, 1, wx.EXPAND)
        gen_settings_sizer.Add(wx.StaticText(self, label='Number of positions:'))
        gen_settings_sizer.Add(self.positions_ctrl)


        self.valve_position = utils.IntSpinCtrl(self)
        self.valve_position.Bind(utils.EVT_MY_SPIN, self._on_position_change)

        gen_results_sizer = wx.FlexGridSizer(rows=1, cols=2, vgap=2, hgap=2)
        gen_results_sizer.AddGrowableCol(1)
        gen_results_sizer.Add(wx.StaticText(self, label='Valve position:'))
        gen_results_sizer.Add(self.valve_position)

        self.connect_button = wx.Button(self, label='Connect')
        self.connect_button.Bind(wx.EVT_BUTTON, self._on_connect)

        self.settings_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Settings'),
            wx.VERTICAL)
        self.settings_box_sizer.Add(gen_settings_sizer, flag=wx.EXPAND)
        self.settings_box_sizer.Add(self.connect_button, flag=wx.ALIGN_CENTER_HORIZONTAL|wx.TOP, border=2)

        self.results_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Controls'),
            wx.VERTICAL)
        self.results_box_sizer.Add(gen_results_sizer, flag=wx.EXPAND)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(status_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.results_box_sizer, flag=wx.EXPAND)
        top_sizer.Add(self.settings_box_sizer, flag=wx.EXPAND)

        self.Refresh()

        return top_sizer

    def _initvalve(self, valve_type, comport, valve_args, valve_kwargs):
        """
        Initializes the flow meter parameters if any were provided. If enough are
        provided the flow meter is automatically connected.

        :param str valve_type: The flow meter type, corresponding to a ``known_valve``.

        :param str comport: The comport the flow meter is attached to.

        :param list valve_args: The flow meter positional initialization values.
            Appropriate values depend on the flow meter.

        :param dict valve_kwargs: The flow meter key word arguments. Appropriate
            values depend on the flow meter.
        """
        my_valves = [item.replace('_', ' ') for item in self.known_valves.keys()]
        if valve_type in my_valves:
            self.type_ctrl.SetStringSelection(valve_type)

        if comport in self.all_comports:
            self.com_ctrl.SetStringSelection(comport)

        if valve_type == 'Rheodyne' or valve_type == 'Soft' or valve_type == 'Cheminert':
            self.valve_position.SetMin(1)

            if 'positions' in valve_kwargs.keys():
                self.positions_ctrl.SetValue(str(valve_kwargs['positions']))

        if valve_type in my_valves and comport in self.all_comports:
            logger.info('Initialized valve %s on startup', self.name)
            self._connect()

        elif valve_type == 'Soft':
            logger.info('Initialized valve %s on startup', self.name)
            self._connect()

    def _on_type(self, evt):
        """Called when the valve type is changed in the GUI."""
        valve = self.type_ctrl.GetStringSelection()
        logger.info('Changed the valve type to %s for valve %s', valve, self.name)

    def _on_connect(self, evt):
        """Called when a valve is connected in the GUI."""
        self._connect()

    def _connect(self):
        """Initializes the valve in the FlowMeterCommThread"""
        com = self.com_ctrl.GetStringSelection()
        valve = self.type_ctrl.GetStringSelection().replace(' ', '_')

        args = (com, self.name)
        kwargs = {'positions': int(self.positions_ctrl.GetValue()),
            'comm_lock' : self.comm_lock}

        logger.info('Connected to valve %s', self.name)
        self.connected = True
        self.connect_button.SetLabel('Reconnect')
        # self._send_cmd('connect')
        # self._set_status('Connected')

        if valve == 'Rheodyne' or valve == 'Soft':
            self.valve_position.SetMin(1)
            self.valve_position.SetMax(int(self.positions_ctrl.GetValue()))

        try:
            self.valve = self.known_valves[valve](*args, **kwargs)
            self._set_status('Connected')
            self._send_cmd('add_valve')
        except Exception as e:
            logger.error(e)
            self._set_status('Connection Failed')

        self._position_timer.Start(1000)
        answer_thread = threading.Thread(target=self._wait_for_answer)
        answer_thread.daemon = True
        answer_thread.start()

        return

    def _set_status(self, status):
        """
        Changes the status in the GUI.

        :param str status: The status to display.
        """
        logger.debug('Setting valve %s status to %s', self.name, status)
        self.status.SetLabel(status)

    def _on_position_timer(self, evt):
        """
        Called every 1000 ms when the valve is connected. It gets the
        position, in case it's been changed locally on the valve.
        """
        # self._send_cmd('get_position')
        pos = self.valve.get_position()

        try:
            pos = int(pos)
            if pos != self.position:
                wx.CallAfter(self.valve_position.SetValue, pos)
                self.position = pos
        except Exception:
            pass

    def _on_position_change(self, evt):
        self._position_timer.Stop()
        self._send_cmd('set_position')
        self.position = int(self.valve_position.GetValue())

        wx.CallLater(2000, self._position_timer.Start, 1000)

    def _send_cmd(self, cmd):
        """
        Sends commands to the pump using the ``valve_cmd_q`` that was given
        to :py:class:`FlowMeterCommThread`.

        :param str cmd: The command to send, matching the command in the
            :py:class:`FlowMeterCommThread` ``_commands`` dictionary.
        """
        logger.debug('Sending valve %s command %s', self.name, cmd)
        if cmd == 'get_position':
            self.valve_cmd_q.append(('get_position', (self.name,), {}))
        elif cmd == 'set_position':
            self.valve_cmd_q.append(('set_position', (self.name, int(self.valve_position.GetValue())), {}))
        elif cmd == 'connect':
            com = self.com_ctrl.GetStringSelection()
            valve = self.type_ctrl.GetStringSelection().replace(' ', '_')

            args = (com, self.name, valve)
            kwargs = {'positions': int(self.positions_ctrl.GetValue())}

            self.valve_cmd_q.append(('connect', args, kwargs))
        elif cmd == 'add_valve':
            args = (self.valve, self.name)

            self.valve_cmd_q.append(('add_valve', args, {}))


    def _wait_for_answer(self):
        while True:
            if len(self.answer_q) > 0:
                answer = self.answer_q.popleft()
                if answer[0] == 'position' and answer[1] == self.name:
                    try:
                        pos = int(answer[2])
                        logger.debug("Got valve %s position %i", self.name, pos)
                        wx.CallAfter(self.valve_position.SetValue, pos)
                    except Exception:
                        pass
                elif answer[0] == 'position' and answer[1] != self.name:
                    try:
                        pos = int(answer[2])
                        logger.debug("Got wrong valve position, valve %s position %i", self.name, pos)
                    except Exception:
                        pass
            else:
                time.sleep(0.05)

    def stop(self):
        self._position_timer.Stop()


class ValveFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of valves.
    Only meant to be used when the fscon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, comm_locks, setup_valves, *args, **kwargs):
        """
        Initializes the valve frame. Takes args and kwargs for the wx.Frame class.
        """
        super(ValveFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the ValveFrame')
        self.valve_cmd_q = deque()
        self.valve_return_q = deque()
        self.abort_event = threading.Event()
        self.valve_con = ValveCommThread(self.valve_cmd_q, self.valve_return_q,
            self.abort_event, 'ValveCon')
        self.valve_con.start()

        self.comm_locks = comm_locks

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._get_ports()

        self.valves =[]

        self._create_layout()

        self.Fit()
        self.Raise()

        self._initvalves(setup_valves)

    def _create_layout(self):
        """Creates the layout"""

        self.top_panel = wx.Panel(self)

        valve_panel = ValvePanel(self.top_panel, wx.ID_ANY, 'stand_in', self.ports,
            self.valve_cmd_q, self.valve_return_q, self.valve_con.known_valves, 'stand_in')

        self.valve_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.valve_sizer.Add(valve_panel, flag=wx.RESERVE_SPACE_EVEN_IF_HIDDEN)

        self.valve_sizer.Hide(valve_panel, recursive=True)

        button_panel = wx.Panel(self.top_panel)

        add_valve = wx.Button(button_panel, label='Add valve')
        add_valve.Bind(wx.EVT_BUTTON, self._on_addvalve)

        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        button_sizer.Add(add_valve)

        button_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        button_panel_sizer.Add(wx.StaticLine(button_panel), flag=wx.EXPAND|wx.TOP|wx.BOTTOM, border=2)
        button_panel_sizer.Add(button_sizer, flag=wx.ALIGN_RIGHT|wx.BOTTOM|wx.RIGHT, border=2)

        button_panel.SetSizer(button_panel_sizer)

        top_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        top_panel_sizer.Add(self.valve_sizer, flag=wx.EXPAND)
        top_panel_sizer.Add(button_panel, flag=wx.EXPAND)

        self.top_panel.SetSizer(top_panel_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(self.top_panel, flag=wx.EXPAND, proportion=1)

        self.SetSizer(top_sizer)

    def _initvalves(self, setup_valves):
        """
        This is a convenience function for initalizing flow meters on startup, if you
        already know what flow meters you want to add. You can comment it out in
        the ``__init__`` if you want to not load any flow meters on startup.

        If you want to add flow meters here, add them to the ``setup_valvess`` list.
        Each entry should be an iterable with the following parameters: name,
        flow meter type, comport, arg list, and kwarg dict in that order. How the
        arg list and kwarg dict are handled are defined in the
        :py:func:`ValvePanel._initfm` function, and depends on the flow meter type.
        """
        if not self.valves:
            self.valve_sizer.Remove(0)

        if setup_valves is None:
            setup_valves = [('Injection', 'Rheodyne', 'COM8', [], {'positions' : 2}),
                # ('Sample', 'Rheodyne', 'COM7', [], {'positions' : 6}),
                # ('Buffer 1', 'Rheodyne', 'COM8', [], {'positions' : 6}),
                # ('Buffer 2', 'Rheodyne', 'COM9', [], {'positions' : 6}),
                        ]

            # setup_valves = [('Injection', 'Soft', '', [], {'positions' : 2}),
            #     ('Sample', 'Soft', '', [], {'positions' : 6}),
            #     ('Buffer 1', 'Soft', '', [], {'positions' : 6}),
            #     ('Buffer 2', 'Soft', '', [], {'positions' : 6}),
            #     ]

            # setup_valves = [('Buffer', 'Cheminert', 'COM7', [], {'positions': 10})]

        logger.info('Initializing %s valves on startup', str(len(setup_valves)))

        for valve in setup_valves:
            self._add_valve(valve)

            # new_valve = ValvePanel(self, wx.ID_ANY, valve[0], self.ports, self.valve_cmd_q,
            #     self.valve_return_q, self.valve_con.known_valves, valve[0], valve[1], valve[2], valve[3], valve[4])

            # self.valve_sizer.Add(new_valve)
            # self.valves.append(new_valve)

        self.Layout()
        self.Fit()

    def _on_addvalve(self, evt):
        """
        Called when the Add Valve button is used. Adds a new fvalve
        to the control panel.

        .. note:: Valve names must be distinct.
        """
        if not self.valves:
            self.valve_sizer.Remove(0)

        dlg = wx.TextEntryDialog(self, "Enter valve name:", "Create new valve")
        if dlg.ShowModal() == wx.ID_OK:
            name = dlg.GetValue()
            for valve in self.valves:
                if name == valve.name:
                    msg = "Valve names must be distinct. Please choose a different name."
                    wx.MessageBox(msg, "Failed to add valve")
                    logger.debug('Attempted to add a valve with the same name (%s) as another pump.', name)
                    return


            valve_vals = (name, None, None, [], {})
            self._add_valve(valve_vals)

            logger.info('Added new valve %s to the flow meter control panel.', name)

            self.Layout()
            self.Fit()

        return

    def _add_valve(self, valve):
        if valve[0] in self.comm_locks:
            comm_lock = self.comm_locks[valve[0]]
        else:
            comm_lock = threading.Lock()

        new_valve = ValvePanel(self.top_panel, wx.ID_ANY, valve[0], self.ports,
            self.valve_cmd_q, self.valve_return_q, self.valve_con.known_valves,
            valve[0], valve[1], valve[2], valve[3], valve[4], comm_lock)

        self.valve_sizer.Add(new_valve)
        self.valves.append(new_valve)

    def _get_ports(self):
        """
        Gets a list of active comports.

        .. note:: This doesn't update after the program is opened, so you need
            to start the program after all pumps are connected to the computer.
        """
        port_info = list_ports.comports()
        self.ports = [port.device for port in port_info]

        logger.debug('Found the following comports for the ValveFrame: %s', ' '.join(self.ports))

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the ValveFrame')

        for valve in self.valves:
            valve.stop()

        self.valve_con.stop()
        while self.valve_con.is_alive():
            time.sleep(0.001)
        self.Destroy()

if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    # my_rv67 = RheodyneValve('/dev/cu.usbserial-AC01UZ8O', '6p7_1', 6)
    # my_rv67.get_position()
    # my_rv67.set_position(4)

    # my_vici = CheminertValve('COM3', 'vici1', 10)

    # valve_cmd_q = deque()
    # valve_return_q = deque()
    # abort_event = threading.Event()
    # my_valvecon = ValveCommThread(valve_cmd_q, valve_return_q, abort_event, 'ValveCon')
    # my_valvecon.start()
    # init_cmd = ('connect', ('/dev/cu.usbserial-AC01UZ8O', 'r6p7_1', 'Rheodyne'), {'positions' : 6})
    # get_pos_cmd = ('get_position', ('r6p7_1',), {})
    # set_pos_cmd = ('set_position', ('r6p7_1', 5), {})
    # status_cmd = ('get_status', ('r6p7_1',), {})

    # valve_cmd_q.append(init_cmd)
    # time.sleep(0.1)
    # valve_cmd_q.append(get_pos_cmd)
    # valve_cmd_q.append(set_pos_cmd)
    # valve_cmd_q.append(status_cmd)
    # time.sleep(5)
    # my_valvecon.stop()

    # threading.Lock()

    # comm_locks = {'Injection'   : threading.Lock(),
    #     'Sample'    : threading.Lock(),
    #     'Buffer 1'  : threading.Lock(),
    #     'Buffer 2'  : threading.Lock(),
    #     }
    comm_locks = {}

    app = wx.App()
    logger.debug('Setting up wx app')
    frame = ValveFrame(comm_locks, None, None, title='Valve Control')
    frame.Show()
    app.MainLoop()


