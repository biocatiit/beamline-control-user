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
import multiprocessing

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import wx.lib.scrolledpanel as scrolled

import expcon
import coflowcon
import trcon
import metadata
import scancon
import pipeline_ctrl
import spectrometercon
import biohplccon
import autocon

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

        self.component_panels = {}
        self.component_controls = {}

        self.Bind(wx.EVT_CLOSE, self._on_exit)

        self._create_layout()

        self.Fit()
        self.Raise()

    def _FromDIP(self, size):
        # This is a hack to provide easy back compatibility with wxpython < 4.1
        try:
            return self.FromDIP(size)
        except Exception:
            return size

    def _create_layout(self):
        """Creates the layout"""

        if 'automator' not in self.settings['components']:
            self._create_standard_layout()

        else:
            self._create_auto_layout()

        if ('exposure' in self.component_panels
            and 'pipeline' in self.component_controls):

            self.component_panels['exposure'].set_pipeline_ctrl(
                self.component_controls['pipeline'])

    def _create_standard_layout(self):
        top_panel = wx.Panel(self)

        panel_sizer = wx.BoxSizer(wx.VERTICAL)

        component_sizers = self._generate_component_sizers(top_panel)

        if ('exposure' in component_sizers or 'coflow' in component_sizers
            or 'trsaxs_scan' in component_sizers or 'scan' in component_sizers):
            exp_sizer = wx.BoxSizer(wx.HORIZONTAL)

            if ('exposure' in component_sizers
                and 'trsaxs_flow' in component_sizers
                and 'metadata' in component_sizers):

                sub_sub_sizer = wx.BoxSizer(wx.HORIZONTAL)
                sub_sub_sizer.Add(component_sizers['metadata'], proportion=1,
                    border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)
                sub_sub_sizer.Add(component_sizers['exposure'], proportion=2,
                    border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)

                sub_sizer = wx.BoxSizer(wx.VERTICAL)
                sub_sizer.Add(sub_sub_sizer, flag=wx.EXPAND)
                sub_sizer.Add(component_sizers['trsaxs_flow'], proportion=1,
                    border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)

                exp_sizer.Add(sub_sizer, flag=wx.EXPAND, proportion=1)

            elif ('exposure' in component_sizers
                and 'trsaxs_flow' in component_sizers):
                sub_sizer = wx.BoxSizer(wx.VERTICAL)
                sub_sizer.Add(component_sizers['exposure'],
                    border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)
                sub_sizer.Add(component_sizers['trsaxs_flow'], proportion=1,
                    border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)

                exp_sizer.Add(sub_sizer, flag=wx.EXPAND, proportion=1)

            elif ('exposure' in component_sizers
                and 'metadata' in component_sizers):
                exp_sizer.Add(component_sizers['metadata'], proportion=1,
                    border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)
                exp_sizer.Add(component_sizers['exposure'], proportion=2,
                    border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)

            elif 'exposure' in component_sizers:
                exp_sizer.Add(component_sizers['exposure'], proportion=1,
                    border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)

            if 'coflow' in component_sizers:
                exp_sizer.Add(component_sizers['coflow'],
                    border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)

            if 'trsaxs_scan' in component_sizers:
                exp_sizer.Add(component_sizers['trsaxs_scan'],
                    border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)

            if 'scan' in component_sizers:
                exp_sizer.Add(component_sizers['scan'], border=self._FromDIP(5),
                    flag=wx.EXPAND|wx.ALL)

            if 'uv' in component_sizers:
                exp_sizer.Add(component_sizers['uv'], border=self._FromDIP(5),
                    flag=wx.EXPAND|wx.ALL)

            panel_sizer.Add(exp_sizer, flag=wx.EXPAND)

        top_panel.SetSizer(panel_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(top_panel, flag=wx.EXPAND, proportion=1)

        self.SetSizer(top_sizer)



    def _create_auto_layout(self):
        top_panel = wx.Panel(self)
        self.top_notebook = wx.Notebook(top_panel, style=wx.NB_TOP)

        component_sizers = self._generate_component_sizers(self.top_notebook,
            notebook=True)

        # if 'exposure' in component_sizers and 'metadata' in component_sizers:
        #     exp_sizer = wx.BoxSizer(wx.HORIZONTAL)
        #     msizer = component_sizers.pop('metadata')
        #     esizer = component_sizers.pop('exposure')
        #     exp_sizer.Add(msizer, proportion=1,
        #         border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)
        #     exp_sizer.Add(esizer, proportion=2,
        #         border=self._FromDIP(10), flag=wx.EXPAND|wx.ALL)

        #     component_sizers['exposure'] = exp_sizer

        # Make automator sizer at the end, because automator settings need the callbacks
        # from the other panels
        logger.info('Setting up autmator panel')
        key = 'automator'

        inst_settings = {}

        if 'hplc' in self.settings['components']:
            self.settings[key]['hplc_inst'] = 'hplc'
            hplc_panel = self.component_panels['hplc']
            hplc_automator_callback = hplc_panel.automator_callback
            if hplc_panel._device_type == 'AgilentHPLC2Pumps':
                num_paths = 2
            else:
                num_paths = 1
            inst_settings['hplc'] = {'num_paths': num_paths,
                'automator_callback': hplc_automator_callback}
        if 'coflow' in self.settings['components']:
            coflow_panel = self.component_panels['coflow']
            coflow_automator_callback = coflow_panel.automator_callback
            inst_settings['coflow'] = {'automator_callback': coflow_automator_callback}

        if 'exposure' in self.settings['components']:
            exposure_panel = self.component_panels['exposure']
            exposure_automator_callback = exposure_panel.automator_callback
            inst_settings['exp'] = {'automator_callback': exposure_automator_callback}

        self.settings[key]['instruments'] = inst_settings

        label = key.capitalize()
        box_panel = wx.Panel(self.top_notebook)
        box = wx.StaticBox(box_panel, label=label)
        component_panel = self.settings['components'][key](self.settings[key],
            box, name=key)
        self.component_panels[key] = component_panel

        automator_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        automator_sizer.Add(component_panel, proportion=1,
            border=self._FromDIP(2), flag=wx.EXPAND|wx.ALL)

        box_panel.SetSizer(automator_sizer)

        self.top_notebook.AddPage(box_panel, text='Automator',
            select=True)

        for key, page in component_sizers.items():
            if key == 'hplc' or key == 'uv':
                label = key.upper()
            else:
                label = key.capitalize()
            self.top_notebook.AddPage(page, text=label)

        panel_sizer = wx.BoxSizer(wx.VERTICAL)
        panel_sizer.Add(self.top_notebook, flag=wx.EXPAND, proportion=1)
        top_panel.SetSizer(panel_sizer)

        top_sizer = wx.BoxSizer(wx.VERTICAL)
        top_sizer.Add(top_panel, flag=wx.EXPAND, proportion=1)

        self.SetSizer(top_sizer)

    def _generate_component_sizers(self, top_panel, notebook=False):
        component_sizers = {}

        for key in self.settings['components']:

            if key != 'pipeline' and key != 'automator':
                logger.info('Setting up %s panel', key)
                if key == 'trsaxs_scan':
                    label = 'TRSAXS Scan'

                elif key == 'trsaxs_flow':
                    label ='TRSAXS Flow'

                else:
                    if key == 'hplc' or key == 'uv':
                        label = key.upper()
                    else:
                        label = key.capitalize()

                if notebook:
                    box_panel = scrolled.ScrolledPanel(top_panel)
                else:
                    box_panel = top_panel

                box = wx.StaticBox(box_panel, label=label)
                # box.SetOwnForegroundColour(wx.Colour('firebrick'))

                if key != 'uv' and key != 'hplc':
                    component_panel = self.settings['components'][key](self.settings[key],
                        box, name=key)
                else:
                    component_panel = self.settings['components'][key](box, wx.ID_ANY,
                        self.settings[key], name=key)

                component_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
                component_sizer.Add(component_panel, proportion=1,
                    border=self._FromDIP(2), flag=wx.EXPAND|wx.ALL)

                if notebook:
                    box_panel.SetSizer(component_sizer)
                    box_panel.SetupScrolling()
                    component_sizers[key] = box_panel
                else:
                    component_sizers[key] = component_sizer


                self.component_panels[key] = component_panel

            elif key == 'pipeline':
                logger.info('Setting up pipeline')
                ctrl = self.settings['components'][key](self.settings[key])
                self.component_controls[key] = ctrl
            else:
                pass

        return component_sizers

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the BioFrame')

        for panel in self.component_panels.values():
            panel.on_exit()

        for ctrl in self.component_controls.values():
            ctrl.stop()

        self.Destroy()


if __name__ == '__main__':
    # multiprocessing.set_start_method('spawn')

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.INFO)
    # h1.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    h1.setFormatter(formatter)

    logger.addHandler(h1)


    ###################################################################
    # Exposure
    exposure_settings = expcon.default_exposure_settings

    # # Fast in-air shutters
    # exposure_settings['shutter_speed_open'] = 0.001
    # exposure_settings['shutter_speed_close'] = 0.001
    # exposure_settings['shutter_speed_pad'] = 0.00
    # exposure_settings['shutter_speed_cycle'] = 0.002

    # Normal vacuum shutter (uniblitz)
    exposure_settings['shutter_speed_open'] = 0.0045
    exposure_settings['shutter_speed_close'] = 0.004
    exposure_settings['shutter_speed_pad'] = 0.002
    exposure_settings['shutter_speed_cycle'] = 0.1

    exposure_settings['det_args'] =  {'use_tiff_writer': False,
        'use_file_writer': True, 'photon_energy' : 12.0,
        'images_per_file': 300} #1 image/file for TR, 300 for eq SAXS, 1000 for muscle

    # Muscle settings
    exposure_settings['struck_measurement_time'] = '0.001'
    exposure_settings['tr_muscle_exp'] = False

    #Other settings
    exposure_settings['wait_for_trig'] = True
    exposure_settings['struck_log_vals'] = [
        # Format: (mx_record_name, struck_channel, header_name,
        # scale, offset, use_dark_current, normalize_by_exp_time)
        {'mx_record': 'mcs3', 'channel': 2, 'name': 'I0',
        'scale': 1, 'offset': 0, 'dark': True, 'norm_time': False},
        {'mx_record': 'mcs4', 'channel': 3, 'name': 'I1', 'scale': 1,
        'offset': 0, 'dark': True, 'norm_time': False},
        # {'mx_record': 'mcs5', 'channel': 4, 'name': 'I2', 'scale': 1,
        # 'offset': 0, 'dark': True, 'norm_time': False},
        # {'mx_record': 'mcs6', 'channel': 5, 'name': 'I3', 'scale': 1,
        # 'offset': 0, 'dark': True, 'norm_time': False},
        # {'mx_record': 'mcs7', 'channel': 6, 'name': 'Detector_Enable',
        # 'scale': 1e5, 'offset': 0, 'dark': True, 'norm_time': True},
        # {'mx_record': 'mcs12', 'channel': 11, 'name': 'Length_Out',
        # 'scale': 10e6, 'offset': 0, 'dark': False, 'norm_time': True},
        # {'mx_record': 'mcs13', 'channel': 13, 'name': 'Length_In',
        # 'scale': 10e6, 'offset': 0, 'dark': False, 'norm_time': True},
        # {'mx_record': 'mcs13', 'channel': 12, 'name': 'Force',
        # 'scale': 10e6, 'offset': 0, 'dark': False, 'norm_time': True},
        ]
    exposure_settings['warnings'] = {'shutter' : True, 'col_vac' : {'check': True,
        'thresh': 0.04}, 'guard_vac' : {'check': True, 'thresh': 0.04},
        'sample_vac': {'check': True, 'thresh': 0.04}, 'sc_vac':
        {'check': True, 'thresh':0.04}}
    exposure_settings['base_data_dir'] = '/nas_data/Eiger2x/2025_Run1/2025_01_15_Hopkins' #CHANGE ME and pipeline local_basedir
    exposure_settings['data_dir'] = exposure_settings['base_data_dir']


    ###################################################################
    # Coflow

    coflow_settings = {
        'show_advanced_options'     : False,
        'device_communication'      : 'remote',
        'remote_pump_ip'            : '164.54.204.53',
        'remote_pump_port'          : '5556',
        'remote_fm_ip'              : '164.54.204.53',
        'remote_fm_port'            : '5557',
        'remote_overflow_ip'        : '164.54.204.75',
        'remote_valve_ip'           : '164.54.204.53',
        'remote_valve_port'         : '5558',
        'flow_units'                : 'mL/min',
        'sheath_pump'               : {'name': 'sheath', 'args': ['VICI M50', 'COM6'],
                                        'kwargs': {'flow_cal': '627.72',
                                        'backlash_cal': '9.814'},
                                        'ctrl_args': {'flow_rate': 1}},
        # 'outlet_pump'               : {'name': 'outlet', 'args': ['VICI M50', 'COM4'],
        #                                 'kwargs': {'flow_cal': '628.68',
        #                                 'backlash_cal': '9.962'},
        #                                 'ctrl_args': {'flow_rate': 1}},
        'outlet_pump'               : {'name': 'outlet', 'args': ['OB1 Pump', 'COM7'],
                                        'kwargs': {'ob1_device_name': 'Outlet OB1', 'channel': 1,
                                        'min_pressure': -1000, 'max_pressure': 1000, 'P': -2, 'I': -0.15,
                                        'D': 0, 'bfs_instr_ID': None, 'comm_lock': None,
                                        'calib_path': './resources/ob1_calib.txt'},
                                        'ctrl_args': {}},
        'sheath_fm'                 : {'name': 'sheath', 'args': ['BFS', 'COM5'],
                                        'kwargs':{}},
        'outlet_fm'                 : {'name': 'outlet', 'args': ['BFS', 'COM3'],
                                        'kwargs':{}},
        'sheath_valve'              : {'name': 'Coflow Sheath',
                                        'args':['Cheminert', 'COM4'],
                                        'kwargs': {'positions' : 10}},
        # 'sheath_pump'               : {'name': 'sheath', 'args': ['Soft', None], # Simulated devices for testing
        #                                 'kwargs': {}},
        # 'outlet_pump'               : {'name': 'outlet', 'args': ['Soft', None],
        #                                 'kwargs': {}},
        # 'sheath_fm'                 : {'name': 'sheath', 'args': ['Soft', None],
        #                                 'kwargs':{}},
        # 'outlet_fm'                 : {'name': 'outlet', 'args': ['Soft', None],
        #                                 'kwargs':{}},
        # 'sheath_valve'              : {'name': 'Coflow Sheath',
        #                                 'args': ['Soft', None],
        #                                 'kwargs': {'positions' : 10}},
        'sheath_ratio'              : 0.3,
        'sheath_excess'             : 1.5,
        'sheath_warning_threshold_low'  : 0.8,
        'sheath_warning_threshold_high' : 1.2,
        # 'outlet_warning_threshold_low'  : 0.8,
        # 'outlet_warning_threshold_high' : 1.2,
        'outlet_warning_threshold_low'  : 0.98,
        'outlet_warning_threshold_high' : 1.02,
        'sheath_fr_mult'            : 1,
        'outlet_fr_mult'            : 1,
        # 'outlet_fr_mult'            : -1,
        # 'settling_time'             : 5000, #in ms
        'settling_time'             : 120000, #in ms
        'lc_flow_rate'              : '0.6',
        'show_sheath_warning'       : True,
        'show_outlet_warning'       : True,
        'use_overflow_control'      : True,
        'buffer_change_fr'          : 1.19, #in ml/min
        'buffer_change_vol'         : 12., #in ml
        'air_density_thresh'        : 700, #g/L
        'sheath_valve_water_pos'    : 10,
        'sheath_valve_hellmanex_pos': 8,
        'sheath_valve_ethanol_pos'  : 9,
        }

    trsaxs_settings = {
        'position_units'        : 'mm',
        'speed_units'           : 'mm/s',
        'accel_units'           : 'mm/s^2',
        'time_units'            : 's',
        'x_start'               : 0,
        'x_end'                 : 10,
        'y_start'               : 0,
        'y_end'                 : 0,
        'scan_speed'            : 2,
        'num_scans'             : 1,
        'return_speed'          : 20,
        'scan_acceleration'     : 10,
        'return_acceleration'   : 100,
        'constant_scan_speed'   : True,
        'scan_start_offset_dist': 0,
        'scan_end_offset_dist'  : 0,
        'motor_type'            : 'Newport_XPS',
        'motor_ip'              : '164.54.204.76',
        'motor_port'            : '5001',
        'motor_group_name'      : 'XY',
        'motor_x_name'          : 'XY.X',
        'motor_y_name'          : 'XY.Y',
        'pco_direction'         : 'x',
        'pco_pulse_width'       : D('10'), #In microseconds, opt: 0.2, 1, 2.5, 10
        'pco_encoder_settle_t'  : D('0.075'), #In microseconds, opt: 0.075, 1, 4, 12
        'encoder_resolution'    : D('0.000001'), #for XMS160, in mm
        'encoder_precision'     : 6, #Number of significant decimals in encoder value
        # 'encoder_resolution'    : D('0.00001'), #for GS30V, in mm
        # 'encoder_precision'     : 5, #Number of significant decimals in encoder value
        'min_off_time'          : D('0.001'),
        'x_range'               : (-80, 80),
        'y_range'               : (-5, 25),
        'speed_lim'             : (0, 300),
        'acceleration_lim'      : (0, 2500),
        'remote_pump_ip'        : '164.54.204.8',
        'remote_pump_port'      : '5556',
        'remote_fm_ip'          : '164.54.204.8',
        'remote_fm_port'        : '5557',
        'remote_valve_ip'       : '164.54.204.8',
        'remote_valve_port'     : '5558',
        'device_communication'  : 'remote',
        'injection_valve'       : [{'name': 'Injection', 'args': ['Rheodyne', 'COM6'],  #Chaotic flow
                                    'kwargs': {'positions' : 2}},],
        'sample_valve'          : [],
        'buffer1_valve'         : [],
        'buffer2_valve'         : [],
        # 'sample_pump'           : [{'name': 'Sample', 'args': ['SSI Next Gen', 'COM7'],
        #                             'kwargs': {'flow_rate_scale': 1.0204,
        #                             'flow_rate_offset': 15.346/1000,'scale_type': 'up'},
        #                             'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
        #                             'max_pressure': 1800, 'continuous': True}}],
        # 'buffer1_pump'           : [{'name': 'Buffer 1', 'args': ['SSI Next Gen', 'COM15'],
        #                             'kwargs': {'flow_rate_scale': 1.0478,
        #                             'flow_rate_offset': -72.82/1000,'scale_type': 'up'},
        #                             'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
        #                             'max_pressure': 1800, 'continuous': True}}],
        # 'buffer2_pump'          : [{'name': 'Buffer 2', 'args': ['SSI Next Gen', 'COM9'],
        #                             'kwargs': {'flow_rate_scale': 1.0179,
        #                             'flow_rate_offset': -20.842/10000,'scale_type': 'up'},
        #                             'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
        #                             'max_pressure': 1800, 'continuous': True}}],
        # # 'sample_pump'           : [{'name': 'Sample', 'args': ['SSI Next Gen', 'COM7'],
        # #                             'kwargs': {'flow_rate_scale': 1.01,
        # #                             'flow_rate_offset': 15.346/1000,'scale_type': 'up'},
        # #                             'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
        # #                             'max_pressure': 1800, 'continuous': True}}],
        # # 'buffer1_pump'           : [{'name': 'Buffer 1', 'args': ['SSI Next Gen', 'COM15'],
        # #                             'kwargs': {'flow_rate_scale': 1.024,
        # #                             'flow_rate_offset': -72.82/1000,'scale_type': 'up'},
        # #                             'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
        # #                             'max_pressure': 1800, 'continuous': True}}],
        # # 'buffer2_pump'          : [{'name': 'Buffer 2', 'args': ['SSI Next Gen', 'COM9'],
        # #                             'kwargs': {'flow_rate_scale': 1.009,
        # #                             'flow_rate_offset': -20.842/10000,'scale_type': 'up'},
        # #                             'ctrl_args': {'flow_rate': 0.1, 'flow_accel': 0.0,
        # #                             'max_pressure': 1800, 'continuous': True}}],
        # 'outlet_fm'             : {'name': 'outlet', 'args' : ['BFS', 'COM5'], 'kwargs': {}},
        # 'injection_valve_label' : 'Injection',
        # 'sample_valve_label'    : 'Sample',
        # 'buffer1_valve_label'   : 'Buffer 1',
        # 'buffer2_valve_label'   : 'Buffer 2',
        'injection_valve'       : [{'name': 'Injection', 'args': ['Rheodyne', 'COM6'], # Laminar flow
                                    'kwargs': {'positions' : 2}},],
        'sample_valve'          : [{'name': 'Sample', 'args': ['Rheodyne', 'COM3'],
                                    'kwargs': {'positions' : 6}},],
        'buffer1_valve'         : [{'name': 'Buffer 1', 'args': ['Rheodyne', 'COM10'],
                                    'kwargs': {'positions' : 6}},
                                    {'name': 'Buffer 2', 'args': ['Rheodyne', 'COM4'],
                                    'kwargs': {'positions' : 6}},],
        'buffer2_valve'         : [{'name': 'Sheath 1', 'args': ['Rheodyne', 'COM21'],
                                    'kwargs': {'positions' : 6}},
                                    {'name': 'Sheath 2', 'args': ['Rheodyne', 'COM8'],
                                    'kwargs': {'positions' : 6}},],
        'buffer1_pump'           : [{'name': 'Buffer', 'args': ['Pico Plus', 'COM11'],
                                    'kwargs': {'syringe_id': '3 mL, Medline P.C.',
                                    'pump_address': '00', 'dual_syringe': 'False'},
                                    'ctrl_args': {'flow_rate' : '0.068', 'refill_rate' : '3',
                                    'continuous': False}},],
        'buffer2_pump'          : [{'name': 'Sheath', 'args': ['Pico Plus', 'COM12'],
                                    'kwargs': {'syringe_id': '1 mL, Medline P.C.',
                                    'pump_address': '00', 'dual_syringe': 'False'}, 'ctrl_args':
                                    {'flow_rate' : '0.002', 'refill_rate' : '1',
                                    'continuous': False}},],
        'sample_pump'           : [{'name': 'Sample', 'args': ['Pico Plus', 'COM14'],
                                    'kwargs': {'syringe_id': '1 mL, Medline P.C.',
                                    'pump_address': '00', 'dual_syringe': 'False'}, 'ctrl_args':
                                    {'flow_rate' : '0.009', 'refill_rate' : '1',
                                    'continuous': False}}],
        'outlet_fm'             : {'name': 'outlet', 'args' : ['BFS', 'COM13'], 'kwargs': {}},
        'injection_valve_label' : 'Injection',
        'sample_valve_label'    : 'Sample',
        'buffer1_valve_label'   : 'Buffer',
        'buffer2_valve_label'   : 'Sheath',
        # 'device_communication'  : 'remote',                                         # Simulated
        # 'injection_valve'       : [{'name': 'Injection', 'args': ['Soft', None],    # Simulated Chaotic w/syringe pump
        #                             'kwargs': {'positions' : 2}},],
        # 'sample_valve'          : [{'name': 'Sample', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'buffer1_valve'         : [{'name': 'Buffer 1', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'buffer2_valve'         : [{'name': 'Buffer 2', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'sample_pump'           : [{'name': 'Sample', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '10 mL, Medline P.C.',
        #                                 'flow_rate': 1, 'refill_rate': 10},
        #                                 'ctrl_args': {'continuous': False}},],
        # 'buffer1_pump'          : [{'name': 'Buffer 1', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '20 mL, Medline P.C.',
        #                                 'flow_rate': 1, 'refill_rate': 10},
        #                                 'ctrl_args': {'continuous': False}},],
        # 'buffer2_pump'          : [ {'name': 'Buffer 2', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '20 mL, Medline P.C.',
        #                                 'flow_rate': 1, 'refill_rate': 10},
        #                                 'ctrl_args': {'continuous': False}},],
        # 'outlet_fm'             : {'name': 'outlet', 'args': ['Soft', None], 'kwargs':{}},
        # 'injection_valve_label' : 'Injection',
        # 'sample_valve_label'    : 'Sample',
        # 'buffer1_valve_label'   : 'Buffer 1',
        # 'buffer2_valve_label'   : 'Buffer 2',
        # 'injection_valve'       : [{'name': 'Injection', 'args': ['Soft', None],    # Simulated Chaotic w/continuous pump
        #                             'kwargs': {'positions' : 2}},],
        # 'sample_valve'          : [],
        # 'buffer1_valve'         : [],
        # 'buffer2_valve'         : [],
        # 'sample_pump'           : [{'name': 'Sample', 'args': ['Soft', None],
        #                             'kwargs': {}, 'ctrl_args': {'continuous': True}},],
        # 'buffer1_pump'          : [{'name': 'Buffer 1', 'args': ['Soft', None],
        #                             'kwargs': {}, 'ctrl_args': {'continuous': True}},],
        # 'buffer2_pump'          : [{'name': 'Buffer 2', 'args': ['Soft', None],
        #                             'kwargs': {}, 'ctrl_args': {'continuous': True}},],
        # 'outlet_fm'             : {'name': 'outlet', 'args': ['Soft', None], 'kwargs':{}},
        # 'injection_valve_label' : 'Injection',
        # 'sample_valve_label'    : 'Sample',
        # 'buffer1_valve_label'   : 'Buffer 1',
        # 'buffer2_valve_label'   : 'Buffer 2',
        # 'injection_valve'       : [{'name': 'Injection', 'args': ['Soft', None],    # Simulated laminar flow
        #                             'kwargs': {'positions' : 2}},],
        # 'sample_valve'          : [{'name': 'Sample', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'buffer1_valve'         : [{'name': 'Buffer 1', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},
        #                             {'name': 'Buffer 2', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'buffer2_valve'         : [{'name': 'Sheath 1', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},
        #                             {'name': 'Sheath 2', 'args': ['Soft', None],
        #                             'kwargs': {'positions' : 6}},],
        # 'sample_pump'           : [{'name': 'Sample', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '3 mL, Medline P.C.'},
        #                                 'ctrl_args': {'continuous': False,
        #                                 'flow_rate': 1, 'refill_rate': 3}},],
        # 'buffer1_pump'          : [{'name': 'Buffer', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '3 mL, Medline P.C.'},
        #                                 'ctrl_args': {'continuous': False,
        #                                 'flow_rate': 1, 'refill_rate': 3}},],
        # 'buffer2_pump'          : [ {'name': 'Sheath', 'args': ['Soft Syringe', None],
        #                                 'kwargs': {'syringe_id': '3 mL, Medline P.C.'},
        #                                 'ctrl_args': {'continuous': False,
        #                                 'flow_rate': 1, 'refill_rate': 3}},],
        # 'outlet_fm'             : {'name': 'outlet', 'args': ['Soft', None], 'kwargs':{}},
        # 'injection_valve_label' : 'Injection',
        # 'sample_valve_label'    : 'Sample',
        # 'buffer1_valve_label'   : 'Buffer',
        # 'buffer2_valve_label'   : 'Sheath',
        'flow_units'            : 'mL/min',
        'pressure_units'        : 'psi',
        'total_flow_rate'       : '0.149', # For laminar flow
        # 'total_flow_rate'       : '6', # For chaotic flow
        'dilution_ratio'        : '10', # For chaotic flow
        'max_dilution'          : 50, # For chaotic flow
        'max_flow'              : 2, # For laminar flow
        # 'max_flow'              : 8, # For chaotic flow
        'auto_set_valves'       : True,
        'valve_start_positions' : {'sample_valve': 2, 'buffer1_valve': 2,
                                    'buffer2_valve': 2, 'injection_valve': 2},
        'valve_refill_positions': {'sample_valve': 1, 'buffer1_valve': 1,
                                    'buffer2_valve': 1, 'injection_valve': 2},
        'valve_purge_positions' : {'sample_valve': 6, 'buffer1_valve': 6,
                                    'buffer2_valve': 6, 'injection_valve': 2},
        'autostart'             : 'At flow rate',
        'autostart_flow'        : '4.5',
        'autostart_flow_ratio'  : 0.98,
        'autostart_delay'       : '0',
        'autoinject'            : 'After scan',
        'autoinject_scan'       : '5',
        'autoinject_valve_pos'  : 1,
        # 'mixer_type'            : 'chaotic', # laminar or chaotic
        'mixer_type'            : 'laminar', # laminar or chaotic
        'sample_ratio'          : '0.066', # For laminar flow
        'sheath_ratio'          : '0.032', # For laminar flow
        'simulated'             : False, # VERY IMPORTANT. MAKE SURE THIS IS FALSE FOR EXPERIMENTS
        }

    scan_settings = {
        'components'            : ['scan'],
        'newport_ip'            : '164.54.204.76',
        'newport_port'          : '5001',
        'show_advanced_options' : True,
        'motor_group_name'      : 'XY',
        }

    metadata_settings = {
        'components'        : ['metadata'],
        'saxs_defaults'     : {'exp_type'   : 'SEC-SAXS',
                                'buffer'    : '',
                                'sample'    : '',
                                'temp'      : 22,
                                'volume'    : '',
                                'conc'      : '',
                                'column'    : 'Superdex 200 10/300 Increase',
                                'is_buffer' : False,
                                'mixer'     : 'Chaotic S-bend (90 ms)',
                                'notes'     : '',
                                'separate_buffer'   : False,
                                },
        'muscle_defaults'   : {'system'         : 'Mouse',
                                'muscle_type'   : 'Cardiac',
                                'muscle'        : '',
                                'preparation'   : 'Intact',
                                'notes'         : '',
                                },
        'metadata_type'     : 'auto',
        # 'metadata_type'     : 'muscle',
        }

    pipeline_settings = {
        'components'    : ['pipeline'],
        'server_port'   : '5556',
        'server_ip'     : '164.54.204.142', #EPU
        # 'server_ip'     : '164.54.204.144', #Marvin
        # 'raw_settings'  : '/nas_data/Pilatus1M/2021_Run1/20210129_Hopkins/setup/calibration/pipeline_SAXS.cfg',
        'local_basedir' : '/nas_data/Eiger2x',
        'data_basedir'  : '/nas_data/Eiger2x',
        'output_basedir': '/nas_data/SAXS',
        'data_source'   : 'Stream', #File or stream
        'detector'      : 'Eiger',
        }

    spectrometer_settings = {
        'device_init'           : [{'name': 'CoflowUV', 'args': ['StellarNet', None],
                                    'kwargs': {'shutter_pv_name': '18ID:LJT4:2:Bo11',
                                    'trigger_pv_name' : '18ID:LJT4:2:Bo12',
                                    'out1_pv_name' : '18ID:E1608:Ao1',
                                    'out2_pv_name' : '18ID:E1608:Ao2',
                                    'trigger_in_pv_name' : '18ID:E1608:Bi8'}}],
        'max_int_t'             : 0.025, # in s
        'scan_avg'              : 1,
        'smoothing'             : 0,
        'xtiming'               : 3,
        'spectrum_type'         : 'Absorbance', #Absorbance, Transmission, Raw
        'dark_correct'          : True,
        'auto_dark'             : True,
        'auto_dark_t'           : 60*60, #in s
        'dark_avgs'             : 3,
        'ref_avgs'              : 2,
        'history_t'             : 60*60*24, #in s
        'save_subdir'           : 'UV',
        'save_type'             : 'Absorbance',
        'series_ref_at_start'   : True,
        'drift_correct'         : False,
        'drift_window'          : [750, 800],
        'abs_wav'               : [280, 260],
        'abs_window'            : 3,
        'int_t_scale'           : 2,
        'wavelength_range'      : [225, 838.39],
        'analog_out_v_max'      : 10.,
        'analog_out_au_max'     : 10000, #mAu
        'analog_out_wav'        : {'out1': 280, 'out2': 260},
        'do_ao'                 : True,
        'remote_ip'             : '164.54.204.53',
        'remote_port'           : '5559',
        'remote'                : False,
        'remote_device'         : 'uv',
        'com_thread'            : None,
        'remote_dir_prefix'     : {'local' : '/nas_data', 'remote' : 'Y:\\'},
        'inline_panel'          : True,
        'plot_refresh_t'        : 1, #in s
        'device_communication'      : 'remote',
    }


    hplc_settings = biohplccon.default_hplc_2pump_settings
    hplc_settings['com_thread'] = None
    hplc_settings['remote'] = True
    hplc_settings['remote_device'] = 'hplc'
    hplc_settings['remote_ip'] = '164.54.204.113'
    hplc_settings['remote_port'] = '5556'
    hplc_settings['device_data'] = hplc_settings['device_init'][0]


    automator_settings = autocon.default_automator_settings

    biocon_settings = {}

    components = OrderedDict([
        # ('exposure', expcon.ExpPanel),
        # ('coflow', coflowcon.CoflowPanel),
        # ('trsaxs_scan', trcon.TRScanPanel),
        # ('trsaxs_flow', trcon.TRFlowPanel),
        # ('scan',    scancon.ScanPanel),
        # ('metadata', metadata.ParamPanel),
        # ('pipeline', pipeline_ctrl.PipelineControl),
        # ('uv', spectrometercon.UVPanel),
        ('hplc', biohplccon.HPLCPanel),
        ('automator', autocon.AutoPanel)
        ])

    settings = {
        'coflow'        : coflow_settings,
        'exposure'      : exposure_settings,
        'trsaxs_scan'   : trsaxs_settings,
        'trsaxs_flow'   : trsaxs_settings,
        'scan'          : scan_settings,
        'metadata'      : metadata_settings,
        'pipeline'      : pipeline_settings,
        'uv'            : spectrometer_settings,
        'hplc'          : hplc_settings,
        'automator'     : automator_settings,
        'components'    : components,
        'biocon'        : biocon_settings,
        }


    for key in settings:
        if key != 'components' and key != 'biocon':
            keys = list(settings['components'].keys())
            keys.append('biocon')
            settings[key]['components'] = keys

    app = wx.App()

    standard_paths = wx.StandardPaths.Get() #Can't do this until you start the wx app
    info_dir = standard_paths.GetUserLocalDataDir()

    if not os.path.exists(info_dir):
        os.mkdir(info_dir)

    h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'biocon.log'), maxBytes=10e6, backupCount=5, delay=True)
    h2.setLevel(logging.INFO)
    h2.setLevel(logging.DEBUG)
    formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h2.setFormatter(formatter2)

    logger.addHandler(h2)

    logger.debug('Setting up wx app')
    frame = BioFrame(settings, None, title='BioCAT Control', name='biocon')
    frame.Show()
    app.MainLoop()


