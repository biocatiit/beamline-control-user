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

import serial
import wx

print_lock = threading.Lock()

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
            ret = s.read(s.inWaiting())

        return ret.decode()

    def write(self, data):
        """This warps the Serial.write() function. It encodes the input
        data if necessary.

        :param data: Data to be written to the serial device.
        :type data: str, bytes
        """
        if isinstance(data, str):
            data = data.encode()

        try:
            with self.ser as s:
                s.write(data)
        except ValueError as err:
            with print_lock:
                traceback.print_tb(err.__traceback__)



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

class M50Pump(Pump):
    """This class contains information for initializing and communicating with
    a VICI M50 Pump using an MForce Controller.
    """

    def __init__(self, device, name, flow_cal=628., backlash_cal=1.5):
        """
        This makes the initial serial connection, and then sets the MForce
        controller parameters to the correct values. These correct values are then
        saved in non-volatile memory.

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

        self.pump_comm = SerialComm(device)

        #Make sure parameters are set right
        self.send_cmd('MS=256') #Microstepping to 256, MForce default
        self.send_cmd('VI=1000') #Initial velocity to 1000, MForce default
        self.send_cmd('A=1000000') #Acceleration to 1000000, MForce default
        self.send_cmd('D=1000000') #Deceleration to 1000000, MForce default
        self.send_cmd('HC=5') #Hold current to 5%, MForce default
        self.send_cmd('RC=50') #Run current to 50%, specified by VICI (MForce default is 25%)
        # self.send_cmd('S') #Saves current settings in non-volatile memory

        self._is_flowing = False
        self._is_dispensing = False

        self._flow_cal = flow_cal
        self._backlash_cal = backlash_cal

        self.cal = 51200/self._flow_cal #Calibration value in (micro)steps/uL

    @property
    def flow_rate(self, units='uL/min'):
        """Sets and returns the pump flow rate in uL/min. Can be set while the
        pump is moving, and it will update the flow rate appropriately.

        :type: float
        """
        rate = self._flow_rate*60/self.cal

        if units == 'mL/min':
            rate = rate/1000.
        elif units == 'nL/min':
            rate = rate*1000

        return rate

    @flow_rate.setter
    def flow_rate(self, rate, units='uL/min'):
        if units == 'mL/min':
            rate = rate*1000.
        elif units == 'nL/min':
            rate = rate/1000.

        #Maximum continuous flow rate is 25 mL/min
        if rate>25000:
            rate = 25000
        elif rate<-25000:
            rate = -25000

        #Minimum flow rate is 1 uL/min
        if abs(rate) < 1:
            if rate>0:
                rate = 1
            else:
                rate = -1

        rate = rate/60.
        self._flow_rate = rate*self.cal

        if self._is_flowing:
            self.send_cmd("SL {}".format(self._flow_rate))
        elif self._is_dispensing:
            self.send_cmd("V {}".format(vol))

    def send_cmd(self, cmd):
        """Sends a command to the pump.

        :param cmd: The command to send to the pump.
        :type cmd: str, bytes
        """
        self.pump_comm.write(cmd)

    def wait_for_response(self, timeout=3.):
        """Waits for a response from the pump, up to the set timeout.

        :param timeout: The timeoutat which we stop waiting for a response
        :type timeout: float

        :returns: Response from the pump
        :rtype: str
        """
        response = ''

        start_time = time.time()

        while not response and time.time()-start_time<timeout:
            response = self.get_response()

        return response

    def get_response(self):
        """Gets any and all response from the pump.

        :returns: Response from the pump
        :rtype: str
        """
        response = self.pump_comm.read_all()

        return response

    def is_moving(self):
        """Queries the pump about whether or not it's moving.

        :returns: True if the pump is moving, False otherwise
        :rtype: bool
        """
        self.send_cmd("PR MV")
        status = self.wait_for_response
        status = bool(int(status))

        return status

    def start_flow(self):
        """Starts a continuous flow at the flow rate specified by the
        ``M50Pump.flow_rate`` variable.
        """
        self.send_cmd("SL {}".format(self._flow_rate))
        self.is_flowing = True

    def dispense(self, vol, units='uL'):
        """
        Dispenses a fixed volume.

        :param vol: Volume to dispense
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        if units == 'mL':
            vol = vol*1000.
        elif units == 'nL':
            vol = vol/1000.

        vol = vol*self.cal

        self.send_cmd("V {}".format(self._flow_rate))
        self.send_cmd("MR {}".format(vol))
        self._is_dispensing = True

    def aspirate(self, vol, units='uL'):
        """
        Aspirates a fixed volume.

        :param vol: Volume to aspirate
        :type vol: float

        :param units: Volume units, defaults to uL, also accepts mL or nL
        :type units: str
        """
        self.dispense(-1*vol, units)

    def stop(self):
        """Stops all pump flow."""
        self.send_cmd("SL 0")
        self.send_cmd("\x1B")
        self.is_flowing = False
        self._is_dispensing = False
