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

import logging
import logging.handlers as handlers
import sys
import os
from collections import OrderedDict
from decimal import Decimal as D

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx

import expcon
import coflowcon
import trcon

class BioFrame(wx.Frame):
    """
    A lightweight frame allowing one to work with arbitrary number of pumps.
    Only meant to be used when the pumpcon module is run directly,
    rather than when it is imported into another program.
    """
    def __init__(self, settings, *args, **kwargs):
        """
        Initializes the pump frame. Takes args and kwargs for the wx.Frame class.
        """
        super(BioFrame, self).__init__(*args, **kwargs)
        logger.debug('Setting up the BioFrame')

        self.settings = settings

        self.component_sizers = {}
        self.component_panels = {}

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._create_layout()

        self.Fit()
        self.Raise()

    def _create_layout(self):
        """Creates the layout"""
        top_panel = wx.Panel(self)

        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        for key in self.settings['components']:
            logger.info('Setting up %s panel', key)
            if key == 'trsaxs':
                label = 'TRSAXS'
            else:
                label = key.capitalize()
            box = wx.StaticBox(top_panel, label=label)
            box.SetOwnForegroundColour(wx.Colour('firebrick'))
            component_panel = self.settings['components'][key](self.settings[key], box, name=key)

            component_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
            component_sizer.Add(component_panel, proportion=1, border=2,
                flag=wx.EXPAND|wx.ALL)

            self.component_sizers[key] = component_sizer
            self.component_panels[key] = component_panel

        if ('exposure' in self.component_sizers or 'coflow' in self.component_sizers
            or 'trsaxs' in self.component_sizers):
            exp_sizer = wx.BoxSizer(wx.HORIZONTAL)

            if 'exposure' in self.component_sizers:
                exp_sizer.Add(self.component_sizers['exposure'], proportion=1,
                    border=10, flag=wx.EXPAND|wx.ALL)

            if 'coflow' in self.component_sizers:
                exp_sizer.Add(self.component_sizers['coflow'], border=10,
                    flag=wx.EXPAND|wx.ALL)

            if 'trsaxs' in self.component_sizers:
                exp_sizer.Add(self.component_sizers['trsaxs'], border=10,
                    flag=wx.EXPAND|wx.ALL)

            panel_sizer.Add(exp_sizer, flag=wx.EXPAND)

        top_panel.SetSizer(panel_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(top_panel, flag=wx.EXPAND)

        self.SetSizer(top_sizer)

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the BioFrame')

        for panel in self.component_panels.values():
            panel.on_exit()

        self.Destroy()


if __name__ == '__main__':
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    h1.setFormatter(formatter)

    logger.addHandler(h1)

    #Settings for Pilatus 3X 1M
    exposure_settings = {
        'data_dir'              : '',
        'filename'              : '',
        'run_num'               : 1,
        'exp_time'              : '0.5',
        'exp_period'            : '2',
        'exp_num'               : '5',
        'exp_time_min'          : 0.00105,
        'exp_time_max'          : 5184000,
        'exp_period_min'        : 0.002,
        'exp_period_max'        : 5184000,
        'nframes_max'           : 15000, # For Pilatus: 999999, for Struck: 15000 (set by maxChannels in the driver configuration)
        'nparams_max'           : 15000, # For muscle experiments with Struck, in case it needs to be set separately from nframes_max
        'exp_period_delta'      : 0.00095,
        # 'shutter_speed_open'    : 0.004, #in s
        # 'shutter_speed_close'   : 0.004, # in s
        # 'shutter_pad'           : 0.002, #padding for shutter related values
        # 'shutter_cycle'         : 0.02, #In 1/Hz, i.e. minimum time between shutter openings in a continuous duty cycle
        'shutter_speed_open'    : 0.001, #in s
        'shutter_speed_close'   : 0.001, # in s
        'shutter_pad'           : 0.00, #padding for shutter related values
        'shutter_cycle'         : 0.002, #In 1/Hz, i.e. minimum time between shutter openings in a continuous duty cycle
        'struck_measurement_time' : '0.001', #in s
        'tr_muscle_exp'         : False,
        'slow_mode_thres'       : 0.1,
        'fast_mode_max_exp_time': 2000,
        'wait_for_trig'         : True,
        'num_trig'              : '1',
        'show_advanced_options' : True,
        'fe_shutter_pv'         : 'FE:18:ID:FEshutter',
        'd_shutter_pv'          : 'PA:18ID:STA_D_SDS_OPEN_PL.VAL',
        'local_dir_root'        : '/nas_data/Pilatus1M',
        'remote_dir_root'       : '/nas_data',
        'struck_log_vals'       : [{'mx_record': 'mcs3', 'channel': 2, 'name': 'I0',
            'scale': 1, 'offset': 0, 'dark': True, 'norm_time': False}, #Format: (mx_record_name, struck_channel, header_name, scale, offset, use_dark_current, normalize_by_exp_time)
            {'mx_record': 'mcs4', 'channel': 3, 'name': 'I1', 'scale': 1,
            'offset': 0, 'dark': True, 'norm_time': False},
            {'mx_record': 'mcs5', 'channel': 4, 'name': 'I2', 'scale': 1,
            'offset': 0, 'dark': True, 'norm_time': False},
            {'mx_record': 'mcs6', 'channel': 5, 'name': 'I3', 'scale': 1,
            'offset': 0, 'dark': True, 'norm_time': False},
            {'mx_record': 'mcs11', 'channel': 10, 'name': 'Beam_current',
            'scale': 5000, 'offset': 0.5, 'dark': False, 'norm_time': True},
            # {'mx_record': 'mcs12', 'channel': 11, 'name': 'Flow_rate',
            # 'scale': 10e6, 'offset': 0, 'dark': True, 'norm_time': True},
            {'mx_record': 'mcs7', 'channel': 6, 'name': 'Pilatus_Enable',
            'scale': 1e5, 'offset': 0, 'dark': True, 'norm_time': True},
            {'mx_record': 'mcs12', 'channel': 11, 'name': 'Force',
            'scale': 10e6, 'offset': 0, 'dark': True, 'norm_time': True},
            ],
        'joerger_log_vals'      : [{'mx_record': 'j3', 'name': 'I0',
            'scale': 1, 'offset': 0, 'norm_time': False}, #Format: (mx_record_name, struck_channel, header_name, scale, offset, use_dark_current, normalize_by_exp_time)
            {'mx_record': 'j4', 'name': 'I1', 'scale': 1, 'offset': 0,
            'norm_time': False},
            {'mx_record': 'j5', 'name': 'I2', 'scale': 1, 'offset': 0,
            'norm_time': False},
            {'mx_record': 'j6', 'name': 'I3', 'scale': 1, 'offset': 0,
            'norm_time': False},
            {'mx_record': 'j11', 'name': 'Beam_current', 'scale': 5000,
            'offset': 0.5, 'norm_time': True}
            ],
        'base_data_dir'         : '/nas_data/Pilatus1M/20190326Hopkins', #CHANGE ME
        }

    exposure_settings['data_dir'] = exposure_settings['base_data_dir']

    coflow_settings = {
        'show_advanced_options' : False,
        'device_communication'  : 'remote',
        'remote_pump_ip'        : '164.54.204.37',
        'remote_pump_port'      : '5556',
        'remote_fm_ip'          : '164.54.204.37',
        'remote_fm_port'        : '5557',
        'flow_units'            : 'mL/min',
        'sheath_pump'           : ('VICI_M50', 'COM5', [626.2, 9.278], {}),
        'outlet_pump'           : ('VICI_M50', 'COM6', [623.56, 12.222], {}),
        'sheath_fm'             : ('BFS', 'COM3', [], {}),
        'outlet_fm'             : ('BFS', 'COM4', [], {}),
        'sheath_ratio'          : 0.5,
        'sheath_excess'         : 2.1,
        'warning_threshold_low' : 0.8,
        'warning_threshold_high': 1.2,
        'settling_time'         : 5000, #in ms
        'lc_flow_rate'          : '0.7',
        }

    trsaxs_settings = {
        'components'            : ['time resolved'],
        'position_units'        : 'mm',
        'speed_units'           : 'mm/s',
        'accel_units'           : 'mm/s^2',
        'time_units'            : 's',
        'x_start'               : 0,
        'x_end'                 : 10,
        'y_start'               : 0,
        'y_end'                 : 0,
        'scan_speed'            : 1,
        'num_scans'             : 1,
        'return_speed'          : 1,
        'scan_acceleration'     : 1,
        'return_acceleration'   : 1,
        'constant_scan_speed'   : True,
        'scan_start_offset_dist': 0,
        'scan_end_offset_dist'  : 0,
        'motor_type'            : 'Newport_XPS',
        'motor_ip'              : '164.54.204.65',
        'motor_port'            : '5001',
        'motor_group_name'      : 'XY',
        'motor_x_name'          : 'XY.X',
        'motor_y_name'          : 'XY.Y',
        'pco_direction'         : 'x',
        'pco_pulse_width'       : D('10'), #In microseconds, opt: 0.2, 1, 2.5, 10
        'pco_encoder_settle_t'  : D('12'), #In microseconds, opt: 0.075, 1, 4, 12
        'encoder_resolution'    : D('0.0005'), #for ILS50PP, in mm
        'encoder_precision'     : 4, #Number of significant decimals in encoder value
        'min_off_time'          : D('0.001'),
        'x_range'               : (-25, 25),
        'y_range'               : (-25, 25),
        'speed_lim'             : (0, 50),
        'acceleration_lim'      : (0, 200),
        }

    biocon_settings = {}

    components = OrderedDict([
        ('exposure', expcon.ExpPanel),
        # ('coflow', coflowcon.CoflowPanel),
        # ('trsaxs', trcon.TRPanel),
        ])

    settings = {
        'coflow'        : coflow_settings,
        'exposure'      : exposure_settings,
        'trsaxs'        : trsaxs_settings,
        'components'    : components,
        'biocon'        : biocon_settings
        }


    for key in settings:
        if key != 'components' and key != 'biocon':
            settings[key]['components'] = settings['components'].keys()

    app = wx.App()

    standard_paths = wx.StandardPaths.Get() #Can't do this until you start the wx app
    info_dir = standard_paths.GetUserLocalDataDir()

    if not os.path.exists(info_dir):
        os.mkdir(info_dir)

    h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'expcon.log'), maxBytes=10e6, backupCount=5, delay=True)
    h2.setLevel(logging.INFO)
    formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h2.setFormatter(formatter2)

    logger.addHandler(h2)

    logger.debug('Setting up wx app')
    frame = BioFrame(settings, None, title='BioCAT Control')
    frame.Show()
    app.MainLoop()


