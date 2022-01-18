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

import copy
import time
import logging

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import epics

from epics import Device

import utils

utils.set_mppath() #This must be done before importing any Mp Modules.
import Mp as mp
import MpCa as mpc

class Detector(object):
    def __init__(self):
        """
        """

    # def __repr__(self):
    #     return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    # def __str__(self):
    #     return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    def abort(self):
        pass

    def arm(self):
        pass

    def get_status(self):
        pass

    def set_data_dir(self, data_dir):
        pass

    def set_exp_period(self, exp_period):
        pass

    def set_exp_time(self, exp_time):
        pass

    def set_filename(self, filename):
        pass

    def set_num_frames(self, num_frames):
        pass

    def set_trigger_mode(self, mode):
        pass

    def stop(self):
        pass


class MXDetector(Detector):
    def __init__(self, record, mx_database, data_dir_root):

        self.record_name = record

        self.det = mx_database.get_record('pilatus')

        server_record_name = self.det.get_field('server_record')
        remote_det_name = self.det.get_field('remote_record_name')
        server_record = mx_database.get_record(server_record_name)
        det_datadir_name = '{}.datafile_directory'.format(remote_det_name)
        det_datafile_name = '{}.datafile_pattern'.format(remote_det_name)
        det_exp_time_name = '{}.ext_enable_time'.format(remote_det_name)
        det_exp_period_name = '{}.ext_enable_period'.format(remote_det_name)
        det_local_datafile_root_name = '{}.local_datafile_user'.format(remote_det_name)

        self.det_datadir = mp.Net(server_record, det_datadir_name)
        self.det_filename = mp.Net(server_record, det_datafile_name)
        self.det_exp_time = mp.Net(server_record, det_exp_time_name)
        self.det_exp_period = mp.Net(server_record, det_exp_period_name)

        det_local_datafile_root = mp.Net(server_record, det_local_datafile_root_name)
        det_local_datafile_root.put(data_dir_root) #MX record field is read only?

        self.trigger_mode = 'ext_enable'

    def abort(self):
        self.det.abort()

    def arm(self):
        self.det.arm()

    def get_status(self):
        return self.det.get_status() & 0x1

    def set_data_dir(self, data_dir):
        self.det_datadir.put(data_dir)
        while self.det_datadir.get().rstrip('/') != data_dir.rstrip('/'):
            time.sleep(0.001)

    def set_exp_period(self, exp_period):
        self.det_exp_period.put(exp_period)

    def set_exp_time(self, exp_time):
        self.det_exp_time.put(exp_time)

    def set_filename(self, filename):
        self.det_filename.put(filename)

    def set_num_frames(self, num_frames):
        if self.trigger_mode == 'ext_enable' or self.trigger_mode == 'int_enable':
            self.det.set_duration_mode(num_frames)
        else:
            self.det.set_multiframe_mode(num_frames)

    def set_trigger_mode(self, mode):
        self.trigger_mode = mode

        if mode == 'ext_enable':
            tm = 2
        elif mode == 'ext_trig':
            tm = 2
        elif mode == 'int_trig':
            tm = 1
        elif mode == 'int_enable':
            tm = 1

        self.det.set_trigger_mode(tm)

    def stop(self):
        self.det.stop()

class AD_EigerCamera(Device):
    """
    Basic AreaDetector Camera Device
    """
    attrs = ("Acquire", "AcquirePeriod", "AcquirePeriod_RBV",
             "AcquireTime", "AcquireTime_RBV",
             "AcquireState_RBV",
             "ArrayCallbacks", "ArrayCallbacks_RBV",
             "ArrayCounter", "ArrayCounter_RBV", "ArrayRate_RBV",
             "ArraySizeX_RBV", "ArraySizeY_RBV", "ArraySize_RBV",
             "BinX", "BinX_RBV", "BinY", "BinY_RBV",
             "ColorMode", "ColorMode_RBV",
             "DataType", "DataType_RBV", "DetectorState_RBV",
             "Gain", "Gain_RBV",
             "FWAutoRemove", "FWAutoRemove_RBV",
             "FWEnable", "FWEnable_RBV",
             "FWNamePattern", "FWNamePattern_RBV",
             "FWNImagesPerFile", "FWNImagesPerFile_RBV",
             "ImageMode", "ImageMode_RBV",
             "MaxSizeX_RBV", "MaxSizeY_RBV",
             "MinX", "MinX_RBV", "MinY", "MinY_RBV",
             "NumImages", "NumImagesCounter_RBV", "NumImages_RBV",
             "NumTriggers", "NumTriggers_RBV",
             "PhotonEnergy", "PhotonEnergy_RBV",
             "SaveFiles", "SaveFiles_RBV",
             "SizeX", "SizeX_RBV", "SizeY", "SizeY_RBV",
             "StreamEnable", "StreamEnable_RBV",
             "TIFF1:AutoIncrement", "TIFF1:AutoIncrement_RBV",
             "TIFF1:AutoSave", "TIFF1:AutoSave_RBV",
             "TIFF1:EnableCallbacks", "TIFF1:EnableCallbacks_RBV",
             "TIFF1:FileName", "TIFF1:FileName_RBV",
             "TIFF1:FilePath", "TIFF1:FilePath_RBV", "TIFF1:FileTemplate",
             "TimeRemaining_RBV",
             "TriggerMode", "TriggerMode_RBV", "TriggerSoftware",
             "Trigger", 'ManualTrigger', 'ManualTrigger_RBV',)


    _nonpvs = ('_prefix', '_pvs', '_delim')

    def __init__(self, prefix):
        Device.__init__(self, prefix, delim='', mutable=False,
                              attrs=self.attrs)

    def ensure_value(self, attr, value, wait=False):
        """ensures that an attribute with an associated _RBV value is
        set to the specifed value
        """
        rbv_attr = "%s_RBV" % attr
        if rbv_attr not in self._pvs:
            return self._pvs[attr].put(value, wait=wait)

        if  self._pvs[rbv_attr].get(as_string=True) != value:
            self._pvs[attr].put(value, wait=wait)

class EPICSEigerDetector(object):
    def __init__(self, pv_prefix, use_tiff_writer = True,
        use_file_writer = True, photon_energy = 12.0):
        """
        """

        self.det = AD_EigerCamera(pv_prefix)

        self.use_tiff_writer = use_tiff_writer
        self.use_file_writer = use_file_writer

        if self.use_tiff_writer:
            self.det.put('TIFF1:EnableCallbacks', 1, wait=True, timeout=1)
            self.det.put('cam1:StreamEnable', 1, wait=True, timeout=1)
            self.det.put('TIFF1:FileTemplate', '%s%s_%4.4d.tif', wait=True, timeout=1)
            self.det.put('TIFF1:AutoIncrement', 1, wait=True, timeout=1)
            self.det.put('TIFF1:AutoSave', 1, wait=True, timeout=1)

        else:
            self.det.put('TIFF1:EnableCallbacks', 0, wait=True, timeout=1)

        if self.use_file_writer:
            self.det.put('cam1:FWEnable', 1, wait=True, timeout=1)
            self.det.put('cam1:SaveFiles', 1, wait=True, timeout=1)
            self.det.put('cam1:FWAutoRemove', 1, wait=True, timeout=1)

        else:
            self.det.put('cam1:FWEnable', 0, wait=True, timeout=1)

        self.det.put('cam1:PhotonEnergy', photon_energy*1000, wait=True, timeout=1)

    # def __repr__(self):
    #     return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    # def __str__(self):
    #     return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    def abort(self):
        self.det.put("cam1:Acquire", 0, wait=True, timeout=1)

    def arm(self):
        self.det.put("cam1:Acquire", 1, wait=True, timeout=1)

    def trigger(self, wait=True):
        self.det.put("cam1:Trigger", 1, wait=wait, timeout=1)

    def get_status(self):
        return self.det.get("cam1:DetectorState_RBV")

    def get_data_dir(self):
        return self.det.get('cam1:FilePath', as_string=True)

    def set_data_dir(self, data_dir):
        if self.use_tiff_writer:
            self.det.put('TIFF1:FilePath', data_dir, wait=True, timeout=1)

        if self.use_file_writer:
            self.det.put("cam1:FilePath", data_dir, wait=True, timeout=1)

    def set_exp_period(self, exp_period):
        self.det.put('cam1:AcquirePeriod', exp_period, wait=True, timeout=1)

    def set_exp_time(self, exp_time):
        self.det.put('cam1:AcquireTime', exp_time, wait=True, timeout=1)

    def set_filename(self, filename):
        if self.use_tiff_writer:
            self.det.put('TIFF1:FileName', filename, wait=True, timeout=1)
            self.det.put('TIFF1:FileNumber', 1, wait=True, timeout=1)

        if self.use_file_writer:
            self.det.put("cam1:FWNamePattern", filename, wait=True, timeout=1)

    def set_num_frames(self, num_frames):
        trig_mode = self.det.get('cam1:TriggerMode_RBV', as_string=True)

        logger.debug('trig_mode')

        if trig_mode == 'Internal Series' or trig_mode == 'External Series':
            self.det.put('cam1:NumImages', num_frames, wait=True, timeout=1)

        elif trig_mode == 'Internal Enable' or trig_mode == 'External Enable':
            self.det.put('cam1:NumTriggers', num_frames, wait=True, timeout=1)

        if self.use_file_writer:
            if num_frames < 10000:
                self.det.put('cam1:FWNImagesPerFile', num_frames, wait=True, timeout=1)
            else:
                self.det.put('cam1:FWNImagesPerFile', 10000, wait=True, timeout=1)

    def set_trigger_mode(self, mode):
        if mode == 'ext_enable':
            tm = 'External Enable'
        elif mode == 'ext_trig':
            tm = 'External Series'
        elif mode == 'ext_gate':
            tm = 'External Gate'
        elif mode == 'int_trig':
            tm = 'Internal Series'
        elif mode == 'int_enable':
            tm = 'Internal Enable'

        if mode == 'ext_enable' or mode == 'int_enable':
            self.det.put('cam1:NumImages', 1, wait=True, timeout=1)

        self.det.put("cam1:TriggerMode", tm, wait=True, timeout=1)

    def set_manual_trigger(self, mode):
        self.det.put('cam1:ManualTrigger', mode, wait=True, timeout=1)

    def stop(self):
        self.det.put("cam1:Acquire", 0, wait=True, timeout=1)
