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
import traceback

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import serial
import serial.tools.list_ports as list_ports
from six import string_types

import utils

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

        # logger.debug("Recived '%s' after writing to serial device on port %s", out, self.ser.port)
        logger.debug('Received response from serial device on port %s', self.ser.port)
        return out


class Valve(object):
    """
    This class contains the settings and communication for a generic valve.
    It is intended to be subclassed by other valve classes, which contain
    specific information for communicating with a given pump. A valve object
    can be wrapped in a thread for using a GUI, implimented in :py:class:`ValveCommThread`
    or it can be used directly from the command line.
    """

    def __init__(self, name, device, comm_lock=None):
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

        self.connected = False

        self.connect()

    def __repr__(self):
        return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    def __str__(self):
        return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    def connect(self):
       if not self.connected:
            self.connected = True

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

    def disconnect(self):
        pass


class RheodyneValve(Valve):
    """
    """

    def __init__(self, name, device, positions, comm_lock=None):
        """
        This makes the initial serial connection, and then sets the MForce
        controller parameters to the correct values.

        :param str device: The device comport as sent to pyserial

        :param str name: A unique identifier for the pump
        """
        Valve.__init__(self, name, device, comm_lock=comm_lock)

        logstr = ("Initializing valve {} on port {}".format(self.name,
            self.device))
        logger.info(logstr)

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

    def connect(self):
        if not self.connected:
            with self.comm_lock:
                self.valve_comm = SerialComm(self.device, 19200)

            # self.send_command('M', False) #Homes valve

            self.connected = True

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

        self._position = position

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
        with self.comm_lock:
            ret = self.valve_comm.write(cmd, get_response, send_term_char = '\r', term_char='\r')

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

    def __init__(self, name, device, positions, comm_lock=None, baud=9600):
        """
        """
        self._baud = baud

        Valve.__init__(self, name, device, comm_lock=comm_lock)

        logstr = ("Initializing valve {} on port {}".format(self.name,
            self.device))
        logger.info(logstr)

        # self.send_command('IFM1', False) #Sets the response mode to basic
        # self.send_command('SMA', False) #Sets the rotation mode to auto (shortest)
        # self.send_command('AM3', False) #Sets mode to multiposition valve
        # self.send_command('HM', False) #Homes valve

        self._positions = int(positions)

    def connect(self):
        if not self.connected:
            with self.comm_lock:
                self.valve_comm = SerialComm(self.device, self._baud)

            self.connected = True

    def get_position(self):
        position = self.send_command('CP')[0]

        if 'position' in position.lower():
            if '=' in position.lower():
                position = position.split('=')[-1].strip()
            else:
                position = position.lower().split('is')[-1].strip('"').strip()
        else:
            position = position.strip().lstrip('CP')

        try:
            if self._positions == 2:
                if position == 'A':
                    position = 1
                elif position == 'B':
                    position = 2
            else:
                position = int(position)

            position = '{}'.format(int(position))

            success = True
        except Exception:
            success = False

        if success:
            logger.debug("Valve %s position %s", self.name, position)
        else:
            logger.error('Valve %s could not get position', self.name)
            position = None

        self._position = position

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
            if self._positions == 2:
                if position == 1:
                    position = 'A'
                elif position == 2:
                    position = 'B'
            else:
                position = '{}'.format(position)

            ret, success = self.send_command('GO{}'.format(position))

        return success

    def send_command(self, cmd, get_response=True):
        with self.comm_lock:
            ret = self.valve_comm.write(cmd, get_response, send_term_char = '\r',
                term_char='\r')

        if '' != ret:
            success = True
        else:
            success = True

        return ret, success

class SoftValve(Valve):
    """
    Software valve for testing.
    """

    def __init__(self, name, device, positions, comm_lock=None):
        """
        :param str device: The device comport

        :param str name: A unique identifier for the device
        """

        Valve.__init__(self, name, device, comm_lock=comm_lock)

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

known_valves = {
    'Rheodyne'  : RheodyneValve,
    'Soft'      : SoftValve,
    'Cheminert' : CheminertValve,
    }

class ValveCommThread(utils.CommManager):
    """
    Custom communication thread for valves.
    """

    def __init__(self, name):
        """
        Initializes the custom thread. Important parameters here are the
        list of known commands ``_commands`` and known valves.
        """
        utils.CommManager.__init__(self, name)

        logger.info("Starting valve control thread: %s", self.name)

        self._commands = {
                        'connect'           : self._connect_device,
                        'get_position'      : self._get_position,
                        'set_position'      : self._set_position,
                        'get_status'        : self._get_status,
                        'disconnect'        : self._disconnect_device,
                        'get_position_multi': self._get_position_multiple,
                        'set_position_multi': self._set_position_multiple,
                        }

        self._connected_devices = OrderedDict()
        self._connected_coms = OrderedDict()

        self.known_devices = known_valves

    def _cleanup_devices(self):
        for device in self._connected_devices.values():
            device.stop()

    def _additional_new_comm(self, name):
        pass

    def _get_position(self, name, **kwargs):
        logger.debug("Getting valve %s position", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        val = device.get_position(**kwargs)

        self._return_value((name, cmd, val), comm_name)

        logger.debug("Valve %s position: %s", name, val)

    def _get_position_multiple(self, names, **kwargs):
        logger.debug("Getting multiple valve positions")

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        positions = []
        for name in names:
            device = self._connected_devices[name]
            position = device.get_position(**kwargs)

            positions.append(position)

        self._return_value((names, cmd, [names, positions]),
            comm_name)

    def _get_status(self, name, **kwargs):
        logger.debug("Getting valve %s status", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        status = device.get_status(**kwargs)
        logger.debug("Valve %s status: %f", name, status)

        self._return_value((name, cmd, status), comm_name)

    def _set_position(self, name, val, **kwargs):
        logger.debug("Setting valve %s position", name)

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        device = self._connected_devices[name]
        success = device.set_position(val)
        if success:
            logger.info("Valve %s position set to %s", name, val)
        else:
            logger.info("Failed setting valve %s position to %s", name, val)

        self._return_value((name, cmd, success), comm_name)

    def _set_position_multiple(self, names, positions, **kwargs):
        logger.debug('Setting multiple valve positions')

        comm_name = kwargs.pop('comm_name', None)
        cmd = kwargs.pop('cmd', None)

        success = []
        for i, name in enumerate(names):
            logger.debug("Setting valve %s position", name)
            valve = self._connected_devices[name]
            t_success = valve.set_position(positions[i], **kwargs)
            if t_success:
                logger.info("Valve %s position set to %s", name, positions[i])
            else:
                logger.info("Failed setting valve %s position to %s", name,
                    positions[i])
            success.append(t_success)

        if all(success):
            logger.info('Set all valve positions successfully')

        self._return_value((names, cmd, [names, success]),
            comm_name)


class ValvePanel(utils.DevicePanel):
    """
    """
    def __init__(self, parent, panel_id, settings, *args, **kwargs):
        """
        Valve control GUI panel, can be instance multiple times for multiple valves

        :param wx.Window parent: Parent class for the panel.

        :param int panel_id: wx ID for the panel.

        """

        super(ValvePanel, self).__init__(parent, panel_id, settings,
            *args, **kwargs)

        self.current_position = None


    def _create_layout(self):
        """Creates the layout for the panel."""
        self.status = wx.StaticText(self, label='Not connected')

        status_grid = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2),
            hgap=self._FromDIP(2))
        status_grid.AddGrowableCol(1)
        status_grid.Add(wx.StaticText(self, label='Valve name:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        status_grid.Add(wx.StaticText(self, label=self.name), 1,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)
        status_grid.Add(wx.StaticText(self, label='Status: '))
        status_grid.Add(self.status, 1,
            flag=wx.ALIGN_CENTER_VERTICAL|wx.EXPAND)

        status_sizer = wx.StaticBoxSizer(wx.StaticBox(self, label='Info'),
            wx.VERTICAL)
        status_sizer.Add(status_grid, 1, wx.EXPAND)

        self.valve_position = utils.IntSpinCtrl(self)
        self.valve_position.Bind(utils.EVT_MY_SPIN, self._on_position_change)

        gen_results_sizer = wx.FlexGridSizer(cols=2, vgap=self._FromDIP(2),
            hgap=self._FromDIP(2))
        gen_results_sizer.AddGrowableCol(1)
        gen_results_sizer.Add(wx.StaticText(self, label='Valve position:'),
            flag=wx.ALIGN_CENTER_VERTICAL)
        gen_results_sizer.Add(self.valve_position, flag=wx.ALIGN_CENTER_VERTICAL)

        self.results_box_sizer = wx.StaticBoxSizer(wx.StaticBox(self,
            label='Controls'), wx.VERTICAL)
        self.results_box_sizer.Add(gen_results_sizer, flag=wx.EXPAND)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(status_sizer, border=self._FromDIP(2),
            flag=wx.EXPAND|wx.ALL)
        top_sizer.Add(self.results_box_sizer, border=self._FromDIP(2),
            flag=wx.EXPAND|wx.LEFT|wx.RIGHT|wx.BOTTOM)

        self.Refresh()

        self.SetSizer(top_sizer)

    def _init_device(self, settings):
        """
        Initializes the valve.
        """
        device_data = settings['device_data']
        args = device_data['args']
        kwargs = device_data['kwargs']

        args.insert(0, self.name)

        self.valve_type = args[1]

        self.valve_position.SetMin(1)
        self.valve_position.SetMax(kwargs['positions'])

        connect_cmd = ['connect', args, kwargs]

        self._send_cmd(connect_cmd, True)

        position_cmd = ['get_position', [self.name,], {}]
        ret = self._send_cmd(position_cmd, True)

        if ret is not None:
            self._set_gui_position(ret)

        self._update_status_cmd(position_cmd, 1)

        self._set_status_label('Connected')

        logger.info('Initialized valve %s on startup', self.name)

    def _set_status_label(self, status):
        """
        Changes the status in the GUI.

        :param str status: The status to display.
        """
        logger.debug('Setting valve %s status to %s', self.name, status)
        self.status.SetLabel(status)

    def _set_gui_position(self, position):
        try:
            position = int(position)
            self.valve_position.SafeChangeValue(position)
        except Exception:
            pass

    def _on_position_change(self, evt):
        try:
            pos = int(self.valve_position.GetValue())
            if pos != self.current_position:
                pos_cmd = ['set_position', [self.name, pos], {}]
                self._send_cmd(pos_cmd, False)
                self.current_position = pos
        except Exception:
            pass

    def _set_status(self, cmd, val):
        if cmd == 'get_position':
            if val is not None and int(val) != self.current_position:
                self._set_gui_position(val)
                self.current_position = int(val)

class ValveFrame(utils.DeviceFrame):
    """
    A lightweight frame allowing one to work with arbitrary number of valves.
    Only meant to be used when the valvecon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, name, settings, *args, **kwargs):
        """
        Initializes the valve frame. Takes args and kwargs for the wx.Frame class.
        """
        super(ValveFrame, self).__init__(name, settings, ValvePanel,
            *args, **kwargs)

        # Enable these to init devices on startup
        self.setup_devices = self.settings.pop('device_init', None)

        self._init_devices()


if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h1.setFormatter(formatter)
    logger.addHandler(h1)

    # valve_args = {'name': 'Buffer 1', 'args': ['Cheminert', 'COM3'],
    #         'kwargs': {'positions' : 10}}

    # my_valve = CheminertValve(valve_args['name'], valve_args['args'][1],
    #     valve_args['kwargs']['positions'], baud=9600)

    # my_rv = RheodyneValve('injection', 'COM20', 2)
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


    # Device settings
    # TR-SAXS syringe pump chaotic flow
    # setup_devices = [
    #     {'name': 'Injection', 'args': ['Rheodyne', 'COM6'],
    #         'kwargs': {'positions' : 2}},
    #     ('Sample', 'Rheodyne', 'COM7', [], {'positions' : 6}),
    #     {'name': 'Buffer 1', 'args': ['Rheodyne', 'COM12'],
    #         'kwargs': {'positions' : 6}},
    #     {'name': 'Buffer 2', 'args': ['Rheodyne', 'COM14'],
    #         'kwargs': {'positions' : 6}},
    #     ]

    # # TR-SAXS continuous pump chaotic flow
    # setup_devices = [
    #     {'name': 'Injection', 'args': ['Rheodyne', 'COM6'],
    #         'kwargs': {'positions' : 2}},
    #     ]

    # # Coflow buffer valve
    # setup_devices = [
    #     {'name': 'Buffer', 'args': ['Cheminert', 'COM7'],
    #         'kwargs': {'positions': 10}},
    #     ]

    # # TR-SAXS laminar flow
    setup_devices = [
    {'name': 'Injection', 'args': ['Rheodyne', 'COM6'],
            'kwargs': {'positions' : 2}},
        {'name': 'Buffer 1', 'args': ['Rheodyne', 'COM10'],
            'kwargs': {'positions' : 6}},
        {'name': 'Buffer 2', 'args': ['Rheodyne', 'COM4'],
            'kwargs': {'positions' : 6}},
        {'name': 'Sample', 'args': ['Rheodyne', 'COM3'],
            'kwargs': {'positions' : 6}},
        {'name': 'Sheath 1', 'args': ['Rheodyne', 'COM21'],
            'kwargs': {'positions' : 6}},
        {'name': 'Sheath 2', 'args': ['Rheodyne', 'COM8'],
            'kwargs': {'positions' : 6}},
        ]

    # # New HPLC
    # setup_devices = [
    #     {'name': 'Selector', 'args': ['Cheminert', 'COM5'],
    #         'kwargs': {'positions' : 2}},
    #     {'name': 'Outlet', 'args': ['Cheminert', 'COM3'],
    #         'kwargs': {'positions' : 2}},
    #     {'name': 'Purge 1', 'args': ['Cheminert', 'COM9'],
    #         'kwargs': {'positions' : 4}},
    #     {'name': 'Purge 2', 'args': ['Cheminert', 'COM6'],
    #         'kwargs': {'positions' : 4}},
    #     {'name': 'Buffer 1', 'args': ['Cheminert', 'COM7'],
    #         'kwargs': {'positions' : 10}},
    #     {'name': 'Buffer 2', 'args': ['Cheminert', 'COM4'],
    #         'kwargs': {'positions' : 10}},
    #     ]

    #  # SEC-MALS
    # setup_devices = [
    #     {'name': 'Buffer 1', 'args': ['Cheminert', 'COM3'],
    #         'kwargs': {'positions' : 10}},
    #     {'name': 'Buffer 2', 'args': ['Cheminert', 'COM4'],
    #         'kwargs': {'positions' : 10}},
    #     {'name': 'Purge 1', 'args': ['Rheodyne', 'COM5'],
    #         'kwargs': {'positions' : 6}},
    #     ]

    # # Simulated
    # setup_devices = [
    #     {'name': 'Coflow Sheath', 'args': ['Soft', None], 'kwargs': {'positions': 10}}
    #     ]

    # # Autosampler needle
    # setup_devices = [
    #     {'name': 'Needle', 'args': ['Cheminert', 'COM11'],
    #         'kwargs': {'positions': 10}},
    #     ]

    # # MALS switching
    # setup_devices = [
    #     {'name': 'MALS', 'args': ['Cheminert', 'COM8'],
    #         'kwargs': {'positions': 2}},
    #     ]

    # Local
    com_thread = ValveCommThread('ValveComm')
    com_thread.start()

    # # Remote
    # com_thread = None

    settings = {
        'remote'        : False,
        'remote_device' : 'valve',
        'device_init'   : setup_devices,
        'remote_ip'     : '192.168.1.16',
        'remote_port'   : '5558',
        'com_thread'    : com_thread
        }

    app = wx.App()
    logger.debug('Setting up wx app')
    frame = ValveFrame('ValveFrame', settings, parent=None, title='Valve Control')
    frame.Show()
    app.MainLoop()

    if com_thread is not None:
        com_thread.stop()
        com_thread.join()


