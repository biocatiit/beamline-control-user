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
from builtins import object, range, map
from io import open

import copy
import time
import logging

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import epics

from epics import Device, poll

import utils

utils.set_mppath() #This must be done before importing any Mp Modules.
import Mp as mp

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

        self.det_datadir.get()
        self.det_filename.get()
        self.det_exp_time.get()
        self.det_exp_period.get()

        det_local_datafile_root = mp.Net(server_record, det_local_datafile_root_name)
        det_local_datafile_root.get()
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
        start = time.monotonic()
        while self.det_datadir.get().rstrip('/') != data_dir.rstrip('/'):
            time.sleep(0.01)
            if time.monotonic() - start > 10:
                raise Exception('Unable to set detector data dir')

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
    attrs = ("cam1:Acquire", "cam1:AcquirePeriod", "cam1:AcquirePeriod_RBV",
             "cam1:AcquireTime", "cam1:AcquireTime_RBV",
             "cam1:AcquireState_RBV",
             "cam1:ArrayCallbacks", "cam1:ArrayCallbacks_RBV",
             "cam1:ArrayCounter", "cam1:ArrayCounter_RBV", "cam1:ArrayRate_RBV",
             "cam1:ArraySizeX_RBV", "cam1:ArraySizeY_RBV", "cam1:ArraySize_RBV",
             "cam1:BinX", "cam1:BinX_RBV", "cam1:BinY", "cam1:BinY_RBV",
             "cam1:ColorMode", "cam1:ColorMode_RBV",
             "cam1:DataType", "cam1:DataType_RBV", "cam1:DetectorState_RBV",
             "cam1:Gain", "cam1:Gain_RBV",
             "cam1:FWAutoRemove", "cam1:FWAutoRemove_RBV",
             "cam1:FWEnable", "cam1:FWEnable_RBV",
             "cam1:FWNamePattern", "cam1:FWNamePattern_RBV",
             "cam1:FWNImagesPerFile", "cam1:FWNImagesPerFile_RBV",
             "cam1:ImageMode", "cam1:ImageMode_RBV",
             "cam1:MaxSizeX_RBV", "cam1:MaxSizeY_RBV",
             "cam1:MinX", "cam1:MinX_RBV", "cam1:MinY", "cam1:MinY_RBV",
             "cam1:NumImages", "cam1:NumImagesCounter_RBV", "cam1:NumImages_RBV",
             "cam1:NumTriggers", "cam1:NumTriggers_RBV",
             "cam1:PhotonEnergy", "cam1:PhotonEnergy_RBV",
             "cam1:SaveFiles", "cam1:SaveFiles_RBV",
             "cam1:SizeX", "cam1:SizeX_RBV", "cam1:SizeY", "cam1:SizeY_RBV",
             "cam1:StreamEnable", "cam1:StreamEnable_RBV",
             "cam1:TriggerExposure", "cam1:TriggerExposure_RBV",
             "TIFF1:AutoIncrement", "TIFF1:AutoIncrement_RBV",
             "TIFF1:AutoSave", "TIFF1:AutoSave_RBV",
             "TIFF1:EnableCallbacks", "TIFF1:EnableCallbacks_RBV",
             "TIFF1:FileName", "TIFF1:FileName_RBV",
             "TIFF1:FilePath", "TIFF1:FilePath_RBV", "TIFF1:FileTemplate",
             "cam1:TimeRemaining_RBV",
             "cam1:TriggerMode", "cam1:TriggerMode_RBV", "cam1:TriggerSoftware",
             "cam1:Trigger", 'cam1:ManualTrigger', 'cam1:ManualTrigger_RBV',
             "cam1:FilePath", "TIFF1:FileNumber")


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
        use_file_writer = True, photon_energy = 12.0, images_per_file=100):
        """
        """
        self.det = AD_EigerCamera(pv_prefix)

        self.use_tiff_writer = use_tiff_writer
        self.use_file_writer = use_file_writer
        self.images_per_file = images_per_file

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
        status = self.det.get("cam1:DetectorState_RBV")

        if status == 10:
            status = 0

        return status

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
        trig_mode = self.det.get('cam1:TriggerMode_RBV', as_string=True)

        if trig_mode == 'Internal Enable':
            self.det.put('cam1:TriggerExposure', exp_time, wait=True, timeout=1)
        else:
            self.det.put('cam1:AcquireTime', exp_time, wait=True, timeout=1)

    def set_filename(self, filename):
        if self.use_tiff_writer:
            self.det.put('TIFF1:FileName', filename, wait=True, timeout=1)
            self.det.put('TIFF1:FileNumber', 1, wait=True, timeout=1)

        if self.use_file_writer:
            self.det.put("cam1:FWNamePattern", filename, wait=True, timeout=1)

    def set_num_frames(self, num_frames):
        trig_mode = self.det.get('cam1:TriggerMode_RBV', as_string=True)

        logger.debug(trig_mode)

        if trig_mode == 'Internal Series' or trig_mode == 'External Series':
            self.det.put('cam1:NumImages', num_frames, wait=True, timeout=1)
            self.det.put('cam1:NumTriggers', 1, wait=True, timeout=1)

        elif trig_mode == 'Internal Enable' or trig_mode == 'External Enable':
            self.det.put('cam1:NumTriggers', num_frames, wait=True, timeout=1)

        if self.use_file_writer:
            if num_frames < self.images_per_file:
                self.det.put('cam1:FWNImagesPerFile', num_frames, wait=True, timeout=1)
            else:
                self.det.put('cam1:FWNImagesPerFile', self.images_per_file, wait=True, timeout=1)

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
        # self.det.put("cam1:Acquire", 0, wait=True, timeout=1)
        self.det.put('cam1:Acquire', 0, timeout=1)
        # For some reason this is much faster without the wait=True, in terms of EPICS response
        # So going with that for now. Maybe it's a bug that's been fixed in more
        # Recent versions of pyepics? I should try when I convert to python 3.


class AD_PilatusCamera(Device):
    """
    Basic AreaDetector Camera Device
    """
    attrs = ("cam1:Acquire", "cam1:AcquirePeriod", "cam1:AcquirePeriod_RBV",
             "cam1:AcquireTime", "cam1:AcquireTime_RBV",
             "cam1:ArrayCallbacks", "cam1:ArrayCallbacks_RBV",
             "cam1:ArrayCounter", "cam1:ArrayCounter_RBV", "cam1:ArrayRate_RBV",
             "cam1:ArraySizeX_RBV", "cam1:ArraySizeY_RBV", "cam1:ArraySize_RBV",
             "cam1:BinX", "cam1:BinX_RBV", "cam1:BinY", "cam1:BinY_RBV",
             "cam1:ColorMode", "cam1:ColorMode_RBV",
             "cam1:DataType", "cam1:DataType_RBV", "cam1:DetectorState_RBV",
             "cam1:Gain", "cam1:Gain_RBV",
             "cam1:ImageMode", "cam1:ImageMode_RBV",
             "cam1:MaxSizeX_RBV", "cam1:MaxSizeY_RBV",
             "cam1:MinX", "cam1:MinX_RBV", "cam1:MinY", "cam1:MinY_RBV",
             "cam1:NumImages", "cam1:NumImagesCounter_RBV", "cam1:NumImages_RBV",
             # "cam1:PhotonEnergy", "cam1:PhotonEnergy_RBV",
             "cam1:SizeX", "cam1:SizeX_RBV", "cam1:SizeY", "cam1:SizeY_RBV",
             "cam1:TimeRemaining_RBV",
             "cam1:TriggerMode", "cam1:TriggerMode_RBV",
             "cam1:FilePath", "cam1:FileName"
             )


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

class EPICSPilatusDetector(object):
    def __init__(self, pv_prefix):
        """
        """
        self.det = AD_PilatusCamera(pv_prefix)

        # self.det.put('cam1:PhotonEnergy', photon_energy*1000, wait=True, timeout=1)

    # def __repr__(self):
    #     return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    # def __str__(self):
    #     return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    def abort(self):
        self.det.put("cam1:Acquire", 0, wait=True, timeout=1)

    def arm(self):
        self.det.put("cam1:Acquire", 1, wait=True, timeout=1)

    def get_status(self):
        status = self.det.get("cam1:DetectorState_RBV")

        if status == 10:
            status = 0

        return status

    def get_data_dir(self):
        return self.det.get('cam1:FilePath', as_string=True)

    def set_data_dir(self, data_dir):
        self.det.put("cam1:FilePath", data_dir, wait=True, timeout=1)

    def set_exp_period(self, exp_period):
        self.det.put('cam1:AcquirePeriod', exp_period, wait=True, timeout=1)

    def set_exp_time(self, exp_time):
        self.det.put('cam1:AcquireTime', exp_time, wait=True, timeout=1)

    def set_filename(self, filename):
        self.det.put("cam1:FileName", filename, wait=True, timeout=1)

    def set_num_frames(self, num_frames):
        self.det.put('cam1:NumImages', num_frames, wait=True, timeout=1)

    def set_trigger_mode(self, mode):
        if mode == 'ext_enable':
            tm = 1
        elif mode == 'ext_trig':
            tm = 2
        elif mode == 'ext_gate':
            tm = 3
        elif mode == 'int_trig':
            tm = 0

        self.det.put("cam1:TriggerMode", tm, wait=True, timeout=1)

    def stop(self):
        # self.det.put("cam1:Acquire", 0, wait=True, timeout=1)
        self.det.put('cam1:Acquire', 0, timeout=1)
        # For some reason this is much faster without the wait=True, in terms of EPICS response
        # So going with that for now. Maybe it's a bug that's been fixed in more
        # Recent versions of pyepics? I should try when I convert to python 3.

class AD_MarCCDCamera(Device):
    """
    Basic AreaDetector Camera Device
    """
    attrs = ("cam1:Acquire", "cam1:AcquirePeriod", "cam1:AcquirePeriod_RBV",
             "cam1:AcquireTime", "cam1:AcquireTime_RBV",
             "cam1:ArrayCallbacks", "cam1:ArrayCallbacks_RBV",
             "cam1:ArrayCounter", "cam1:ArrayCounter_RBV", "cam1:ArrayRate_RBV",
             "cam1:ArraySizeX_RBV", "cam1:ArraySizeY_RBV", "cam1:ArraySize_RBV",
             "cam1:BinX", "cam1:BinX_RBV", "cam1:BinY", "cam1:BinY_RBV",
             "cam1:ColorMode", "cam1:ColorMode_RBV",
             "cam1:DataType", "cam1:DataType_RBV", "cam1:DetectorState_RBV",
             "cam1:Gain", "cam1:Gain_RBV",
             "cam1:ImageMode", "cam1:ImageMode_RBV",
             "cam1:MaxSizeX_RBV", "cam1:MaxSizeY_RBV",
             "cam1:MinX", "cam1:MinX_RBV", "cam1:MinY", "cam1:MinY_RBV",
             "cam1:NumImages", "cam1:NumImagesCounter_RBV", "cam1:NumImages_RBV",
             # "cam1:PhotonEnergy", "cam1:PhotonEnergy_RBV",
             "cam1:SizeX", "cam1:SizeX_RBV", "cam1:SizeY", "cam1:SizeY_RBV",
             "cam1:TimeRemaining_RBV",
             "cam1:TriggerMode", "cam1:TriggerMode_RBV",
             "cam1:FilePath",
             "cam1:AutoSave", "cam1:FileNumber", "cam1:AutoIncrement",
             )


    _nonpvs = ('_prefix', '_pvs', '_delim')

    def __init__(self, prefix):
        Device.__init__(self, prefix, delim='', mutable=False,
                              attrs=self.attrs)

        self.prefix = prefix

    def ensure_value(self, attr, value, wait=False):
        """ensures that an attribute with an associated _RBV value is
        set to the specifed value
        """
        rbv_attr = "%s_RBV" % attr
        if rbv_attr not in self._pvs:
            return self._pvs[attr].put(value, wait=wait)

        if  self._pvs[rbv_attr].get(as_string=True) != value:
            self._pvs[attr].put(value, wait=wait)

NUM_POSITIONERS = 4
NUM_TRIGGERS    = 4
NUM_DETECTORS   = 70

class Scan(Device):
    """
    A Device representing an Epics sscan record.
    """

    attrs = ('VAL', 'SMSG', 'CMND', 'NPTS', 'EXSC', 'NAME', 'PDLY',
             'PAUS', 'CPT', 'DDLY', 'FAZE', 'ATIME', 'ACQM', 'ACQT')

    pos_attrs = ('PV', 'SP', 'EP', 'SI', 'CP', 'WD', 'PA', 'AR', 'SM')
    trig_attrs = ('PV', 'NV', 'CD')

    _alias = {'device':      'P1PV',
              'start':       'P1SP',
              'end':         'P1EP',
              'step':        'P1SI',
              'table':       'P1PA',
              'absrel':      'P1AR',
              'mode':        'P1SM',
              'npts':        'NPTS',
              'execute':     'EXSC',
              'trigger':     'T1PV',
              'pause':       'PAUS',
              'current_point':  'CPT'}

    def __init__(self, name, **kwargs):
        """
        Initialize the scan.

        name: The name of the scan record.
        """
        attrs = list(self.attrs)
        for i in range(1, NUM_POSITIONERS+1):
            for a in self.pos_attrs:
                attrs.append('P%i%s' % (i, a))
        for i in range(1, NUM_TRIGGERS+1):
            for a in self.trig_attrs:
                attrs.append('T%i%s' % (i, a))
        for i in range(1, NUM_DETECTORS+1):
            attrs.append('D%2.2iPV' % i)
            attrs.append('D%2.2iCV' % i)
            attrs.append('D%2.2iLV' % i)
            attrs.append('D%2.2iCA' % i)
            # attrs.append('D%2.2iDA' % i)

        Device.__init__(self, name, delim='.', attrs=attrs, **kwargs)
        for attr, pv in Scan._alias.items():
            self.add_pv('%s.%s' % (name,pv), attr)

        # make sure this is really a sscan!
        rectype = self.get('RTYP')
        if rectype != 'sscan':
            raise ScanException("%s is not an Epics Scan" % name)

        self.put('SMSG', '')

    def run(self, wait=False, timeout=86400):
        """
        Execute the scan, optionally waiting for completion

        Arguments
        ---------
        wait     whether to wait for completion, True/False (default False)
        timeout  maximum time to wait in seconds, default=86400 (1 day).

        """
        self.put('EXSC', 1, wait=wait, timeout=timeout)

    def reset(self):
        """Reset scan, clearing positioners, detectors, triggers"""
        self.put('NPTS', 0)
        self.put('ACQM', 0)
        self.put('ACQT', 0)
        self.put('ATIME', 0.1)
        self.detector_delay(0)

        for i in range(1, NUM_TRIGGERS+1):
            self.clear_trigger(i)
        for i in range(1, NUM_POSITIONERS+1):
            self.clear_positioner(i)
        for i in range(1, NUM_DETECTORS+1):
            self.clear_detector(i)
        poll(1.e-3, 1.0)

    def _print(self):
        print('PV = %s' % self.get('P1PV'))
        print('SP = %s' % self.get('P1SP'))
        print('EP = %s' % self.get('P1EP'))
        print('NPTS = %s' % self.get('NPTS'))
        print('T  = %s' % self.get('T1PV'))


    def clear_detector(self, idet=1):
        """completely clear a detector

        Arguments
        ---------
          idet    index of detector (1 through 70, default 1)
        """
        self.put("D%2.2iPV" % idet, '')
        poll(1.e-3, 1.0)

    def add_detector(self, detector):
        """add a detector to a scan definition

        Arguments
        ---------
          detector   name of detector pv

        Returns
        -------
         idet  index of detector set
        """
        idet = None
        for _idet in range(1, NUM_DETECTORS+1):
            poll(1.e-3, 1.0)
            if len(self.get('D%2.2iPV' % _idet)) < 2:
                idet = _idet
                break
        if idet is None:
            raise ScanException("%i Detectors already defined." % (NUM_DETECTORS))
        self.put("D%2.2iPV" % idet, detector, wait=True)
        return idet

    def clear_trigger(self, itrig=1):
        """completely clear a trigger

        Arguments
        ---------
          itrig    index of trigger (1 through 4, default 1)
        """
        self.put("T%iPV" % itrig, '')
        poll(1.e-3, 1.0)

    def add_trigger(self, trigger, value=1.0):
        """add a trigger to a scan definition

        Arguments
        ---------
          trigger   name of trigger pv
          value     value to send to trigger (default 1.0)

        Returns
        -------
           itrig  index of trigger set
        """
        itrig = None
        for _itrig in range(1, NUM_TRIGGERS+1):
            poll(1.e-3, 1.0)
            if len(self.get('T%iPV' % _itrig)) < 2:
                itrig = _itrig
                break
        if itrig is None:
            raise ScanException("%i Triggers already defined." % (NUM_TRIGGERS))

        self.put("T%iPV" % itrig, trigger, wait=True)
        self.put("T%iCD" % itrig, value, wait=True)
        return itrig


    def clear_positioner(self, ipos=1):
        """completely clear a positioner

        Arguments
        ---------
          ipos    index of positioner (1 through 4, default 1)
        """
        for attr in self.pos_attrs:
            nulval = 0
            if attr == 'PV': nulval = ''
            if attr == 'PA': nulval = [0]
            self.put("P%i%s" % (ipos, attr), nulval)
        self.put("R%iPV" % ipos, '')
        poll(1.e-3, 1.0)

    def add_positioner(self, drive, readback=None,
                       start=None, stop=None, step=None,
                       center=None, width=None,
                       mode='linear', absolute=True, array=None):
        """add a positioner to a scan definition

        Arguments
        ----------
         drive     name of drive pv
         readback  name of readback pv (defaults to .RBV if drive ends in .VAL)
         mode      positioner mode ('linear', 'table', fly', default 'linear')
         absolute  whether to use absolute values (True/False, default True)
         start     start value
         stop      stop value
         step      step value
         center    center value
         width     width value
         array     array of values for table or fly mode

        Returns
        -------
         ipos  index of positioner set

        """
        ipos = None
        for _ipos in range(1, NUM_POSITIONERS+1):
            poll(1.e-3, 1.0)
            if len(self.get('P%iPV' % _ipos)) < 2:
                ipos = _ipos
                break
        if ipos is None:
            raise ScanException("%i Positioners already defined." % (NUM_POSITIONERS))

        self.put('P%iPV' % ipos, drive, wait=True)
        if readback is None and drive.endswith('.VAL'):
            readback = drive[:-4] + '.RBV'
        if readback is not None:
            self.put('R%iPV' % ipos, readback)

        # set relative/absolute
        if absolute:
            self.put('P%iAR' % ipos, 0)
        else:
            self.put('P%iAR' % ipos, 1)

        # set mode
        smode = 0
        if mode.lower().startswith('table'):
            smode = 1
        elif mode.lower().startswith('fly'):
            smode = 2
        self.put('P%iSM' % ipos, smode)

        # start, stop, step, center, width
        if start is not None:
            self.put('P%iSP' % ipos, start)
        if stop is not None:
            self.put('P%iEP' % ipos, stop)
        if step is not None:
            self.put('P%iSI' % ipos, step)
        if center is not None:
            self.put('P%iCP' % ipos, center)
        if width is not None:
            self.put('P%iWD' % ipos, width)

        # table or fly mode
        if smode in (1, 2) and array is not None:
            self.put('P%iPA' % ipos, array)
        poll(1.e-3, 1.0)
        return ipos

    def set_positioner(self, ipos, drive=None, readback=None,
                       start=None, stop=None, step=None,
                       center=None, width=None,
                       mode=None, absolute=None, array=None):
        """change a positioner setting in a scan definition
        all settings are optional, and will leave other settings unchanged

        Arguments
        ----------
         drive     name of drive pv
         readback  name of readback pv
         mode      positioner mode ('linear', 'table', fly', default 'linear')
         absolute  whether to use absolute values (True/False, default True)
         start     start value
         stop      stop value
         step      step value
         center    center value
         width     width value
         array     array of values for table or fly mode

        Notes
        -----
         This allows changing a scan, for example:

             s = Scan('XXX:scan1')
             ipos1 = s.add_positioner('XXX:m1.VAL', start=-1, stop=1, step=0.1)
             ....

             s.run()

         Then changing the scan definition with

            s.set_positioner(ipos1, start=0, stop=0.2, step=0.01)
            s.run()
        """
        if ipos is None:
            raise ScanException("must give positioner index")

        if drive is not None:
            self.put('P%iPV' % ipos, drive)
        if readback is not None:
            self.put('R%iPV' % ipos, readback)
        if start is not None:
            self.put('P%iSP' % ipos, start)
        if stop is not None:
            self.put('P%iEP' % ipos, stop)
        if step is not None:
            self.put('P%iSI' % ipos, step)
        if center is not None:
            self.put('P%iCP' % ipos, center)
        if width is not None:
            self.put('P%iWD' % ipos, width)
        if array is not None:
            self.put('P%iPA' % ipos, array)

        if absolute is not None:
            if absolute:
                self.put('P%iAR' % ipos, 0)
            else:
                self.put('P%iAR' % ipos, 1)

        if mode is not None:
            smode = 0
            if mode.lower().startswith('table'):
                smode = 1
            elif mode.lower().startswith('fly'):
                smode = 2
            self.put('P%iSM' % ipos, smode)
        poll(1.e-3, 1.0)


    def after_scan(self, mode):
        """set after scan mode"""
        self.put("PASM", mode, wait=True)

    def positioner_delay(self, pdelay):
        """set positioner delay in seconds"""
        self.put("PDLY", pdelay, wait=True)

    def detector_delay(self, pdelay):
        """set detector delay in seconds"""
        self.put("DDLY", pdelay, wait=True)

    def set_points(self, pts):
        self.put('NPTS', pts, wait=True)

    def get_status(self):
        val = self.get('FAZE')

        if val != 0:
            status = 1
        else:
            status = 0

        return status

    def get_current_point(self):
        return self.get('CPT')

    def get_data_in_progress(self, idet):
        return self.get('D%2.2iCA' % idet)

    def stop(self):
        self.put('EXSC', 0, wait=False)
        self.put('EXSC', 0, wait=False)
        self.put('EXSC', 0, wait=False)

class ScanException(Exception):
    """ raised to indicate a problem with a scan"""
    def __init__(self, msg, *args):
        Exception.__init__(self, *args)
        self.msg = msg
    def __str__(self):
        return str(self.msg)

class EPICSMarCCDDetector(object):
    def __init__(self, pv_prefix, scan_pv=''):
        """
        """
        self.det = AD_MarCCDCamera(pv_prefix)

        if scan_pv:
            self.scan = Scan(scan_pv)
        else:
            self.scan = None

    # def __repr__(self):
    #     return '{}({}, {})'.format(self.__class__.__name__, self.name, self.device)

    # def __str__(self):
    #     return '{} {}, connected to {}'.format(self.__class__.__name__, self.name, self.device)

    def abort(self):
        self.det.put("cam1:Acquire", 0, wait=True, timeout=1)

    def arm(self):
        self.det.put("cam1:Acquire", 1, wait=True, timeout=1)

    def get_status(self):
        status = self.det.get("cam1:DetectorState_RBV")
        return status

    def get_data_dir(self):
        return self.det.get('cam1:FilePath', as_string=True)

    def set_data_dir(self, data_dir):
        self.det.put("cam1:FilePath", data_dir, wait=True, timeout=1)

    def set_exp_period(self, exp_period):
        self.det.put('cam1:AcquirePeriod', exp_period, wait=True, timeout=1)

    def set_exp_time(self, exp_time):
        self.det.put('cam1:AcquireTime', exp_time, wait=True, timeout=1)

    def set_filename(self, filename):
        self.det.put("cam1:FileName", filename, wait=True, timeout=1)
        self.det.put("cam1:FileNumber", 1, wait=True, timeout=1)
        self.det.put("cam1:AutoIncrement", 1, wait=True, timeout=1)

    def set_num_frames(self, num_frames):
        self.det.put('cam1:NumImages', num_frames, wait=True, timeout=1)

    def set_trigger_mode(self, mode):
        if mode == 'internal':
            mode = 0
        elif mode == 'frame':
            mode = 1

        self.det.put("cam1:TriggerMode", mode, wait=True, timeout=1)

    def set_image_mode(self, mode):
        if mode == 'single':
            mode = 0
        elif mode == 'multiple':
            mode = 1
        elif mode == 'continuous':
            mode = 2

        self.det.put("cam1:ImageMode", mode, wait=True, timeout=1)

    def set_frame_type(self, mode):
        if mode == 'normal':
            mode = 0
        elif mode == 'bg':
            mode = 1
        elif mode == 'raw':
            mode = 2
        elif mode == 'dblcor':
            mode = 3

        self.det.put("cam1:FrameType", mode, wait=True, timeout=1)

    def set_file_auto_save(self, autosave):
        if autosave:
            self.det.put("cam1:AutoSave", 1)
        else:
            self.det.put("cam1:AutoSave", 0)

    def stop(self):
        self.abort()


class Scaler(Device):
    """
    Simple implementation of SynApps Scaler Record.
    """
    attrs = ('CNT', 'CONT', 'TP', 'T', 'VAL')
    attr_kws = {'calc_enable': '%s_calcEnable.VAL'}
    chan_attrs = ('NM%i', 'S%i')
    calc_attrs = {'calc%i': '%s_calc%i.VAL', 'expr%i': '%s_calc%i.CALC'}
    _nonpvs = ('_prefix', '_pvs', '_delim', '_nchan', '_chans')

    def __init__(self, prefix, nchan=8):
        self._nchan  = nchan
        self._chans = range(1, nchan+1)

        attrs = list(self.attrs)
        for i in self._chans:
            for att in self.chan_attrs:
                attrs.append(att % i)

        Device.__init__(self, prefix, delim='.', attrs=attrs)

        for key, val in self.attr_kws.items():
            self.add_pv(val % prefix, attr= key)

        for i in self._chans:
            for key, val in self.calc_attrs.items():
                self.add_pv(val % (prefix, i), attr = key % i)
        self._mutable = False

    def auto_count_mode(self):
        "set to autocount mode"
        self.put('CONT', 1)

    def one_shot_mode(self):
        "set to one shot mode"
        self.put('CONT', 0)

    def count_time(self, ctime):
        "set count time"
        self.put('TP', ctime)

    def count(self, ctime=None, wait=False):
        "set count, with optional counttime"
        if ctime is not None:
            self.count_time(ctime)
        self.put('CNT', 1, wait=wait)
        poll()

    def enable_calcs(self):
        " enable calculations"
        self.put('calc_enable', 1)

    def set_calc(self, i, calc):
        "set the calculation for scaler i"
        attr = 'expr%i'  % i
        self.put(attr, calc)

    def get_names(self):
        "get all names"
        return [self.get('NM%i' % i) for i in self._chans]

    def read_all(self, use_calc=False):
        "read all values"
        return [self.read(i) for i in self._chans]

    def read(self, ctr, use_calc=False):
        attr = 'S%i'
        if use_calc:
            attr = 'calc%i'
        return self.get(attr % ctr)

    def stop(self):
        self.put('CNT', 0, wait=False)
