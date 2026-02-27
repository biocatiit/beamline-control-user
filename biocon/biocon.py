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

import logging
import logging.handlers as handlers
import sys
import os
from collections import OrderedDict

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx
import wx.lib.scrolledpanel as scrolled

import expcon
import coflowcon
import trcon
import metadata
import pipeline_ctrl
import spectrometercon
import biohplccon
import autocon
import autosamplercon
import toastcon
import monotunecon

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

        self.Layout()
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

        if ('exposure' in self.component_panels
            and 'mono_auto_tune' in self.component_controls):

            self.component_panels['exposure'].set_mono_auto_tune_ctrl(
                self.component_controls['mono_auto_tune'])

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
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)
                sub_sub_sizer.Add(component_sizers['exposure'], proportion=2,
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)

                sub_sizer = wx.BoxSizer(wx.VERTICAL)
                sub_sizer.Add(sub_sub_sizer, flag=wx.EXPAND)
                sub_sizer.Add(component_sizers['trsaxs_flow'], proportion=1,
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)

                exp_sizer.Add(sub_sizer, flag=wx.EXPAND, proportion=1)

            elif ('exposure' in component_sizers
                and 'trsaxs_flow' in component_sizers):
                sub_sizer = wx.BoxSizer(wx.VERTICAL)
                sub_sizer.Add(component_sizers['exposure'],
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)
                sub_sizer.Add(component_sizers['trsaxs_flow'], proportion=1,
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)

                exp_sizer.Add(sub_sizer, flag=wx.EXPAND, proportion=1)

            elif ('exposure' in component_sizers and 'uv' in component_sizers and
                'metadata' in component_sizers and 'coflow' in component_sizers):
                sub_sizer = wx.BoxSizer(wx.VERTICAL)

                sub_sub_sizer1 = wx.BoxSizer(wx.HORIZONTAL)
                sub_sub_sizer1.Add(component_sizers['metadata'],
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL, proportion=1)
                sub_sub_sizer1.Add(component_sizers['exposure'],
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL, proportion=2)

                sub_sub_sizer2 = wx.BoxSizer(wx.HORIZONTAL)
                sub_sub_sizer2.Add(component_sizers['uv'],
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)
                sub_sub_sizer2.Add(component_sizers['coflow'],
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)

                sub_sizer.Add(sub_sub_sizer1, flag=wx.EXPAND)
                sub_sizer.Add(sub_sub_sizer2, flag=wx.EXPAND)

                exp_sizer.Add(sub_sizer, flag=wx.EXPAND, proportion=1)

            elif ('exposure' in component_sizers
                and 'metadata' in component_sizers):
                exp_sizer.Add(component_sizers['metadata'], proportion=1,
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)
                exp_sizer.Add(component_sizers['exposure'], proportion=2,
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)

            elif 'exposure' in component_sizers:
                exp_sizer.Add(component_sizers['exposure'], proportion=1,
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)

            if 'coflow' in component_sizers:
                if not ('exposure' in component_sizers and 'uv' in component_sizers and
                'metadata' in component_sizers and 'coflow' in component_sizers):
                    exp_sizer.Add(component_sizers['coflow'],
                        border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)

            if 'trsaxs_scan' in component_sizers:
                exp_sizer.Add(component_sizers['trsaxs_scan'],
                    border=self._FromDIP(5), flag=wx.EXPAND|wx.ALL)

            if 'scan' in component_sizers:
                exp_sizer.Add(component_sizers['scan'], border=self._FromDIP(5),
                    flag=wx.EXPAND|wx.ALL)

            if 'uv' in component_sizers:
                if not ('exposure' in component_sizers and 'uv' in component_sizers and
                'metadata' in component_sizers and 'coflow' in component_sizers):
                    exp_sizer.Add(component_sizers['uv'], border=self._FromDIP(5),
                        flag=wx.EXPAND|wx.ALL)

            if 'toaster' in component_sizers:
                exp_sizer.Add(component_sizers['toaster'], border=self._FromDIP(5),
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
        logger.info('Setting up automator panel')
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
                'automator_callback': hplc_automator_callback,
                'hplc_panel'    : hplc_panel,}

        if 'coflow' in self.settings['components']:
            coflow_panel = self.component_panels['coflow']
            coflow_automator_callback = coflow_panel.automator_callback
            inst_settings['coflow'] = {'automator_callback': coflow_automator_callback}

        if 'exposure' in self.settings['components']:
            exposure_panel = self.component_panels['exposure']
            exposure_automator_callback = exposure_panel.automator_callback
            inst_settings['exp'] = {'automator_callback': exposure_automator_callback}

        if 'autosampler' in self.settings['components']:
            autosampler_panel = self.component_panels['autosampler']
            autosampler_automator_callback = autosampler_panel.automator_callback
            inst_settings['autosampler'] = {'automator_callback': autosampler_automator_callback}

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

                if (key != 'uv' and key != 'hplc' and key != 'coflow'
                    and key != 'autosampler'):
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
            elif key == 'mono_auto_tune':
                logger.info('Setting up mono auto tune')
                ctrl = self.settings['components'][key](self.settings[key])
                self.component_controls[key] = ctrl
            else:
                pass

        return component_sizers

    def _on_exit(self, evt):
        """Stops all current pump motions and then closes the frame."""
        logger.debug('Closing the BioFrame')

        for panel in self.component_panels.values():
            try:
                panel.on_exit()
            except Exception:
                logger.exception('Error on closing')

        for ctrl in self.component_controls.values():
            try:
                ctrl.stop()
            except Exception:
                logger.exception('Error on closing')


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
    # Exposure settings
    exposure_settings = expcon.default_exposure_settings

    # # Fast in-air shutters
    # exposure_settings['shutter_speed_open'] = 0.001
    # exposure_settings['shutter_speed_close'] = 0.001
    # exposure_settings['shutter_pad'] = 0.00
    # exposure_settings['shutter_cycle'] = 0.002

    # Normal vacuum shutter (uniblitz)
    exposure_settings['shutter_speed_open'] = 0.0045
    exposure_settings['shutter_speed_close'] = 0.004
    exposure_settings['shutter_pad'] = 0.002
    exposure_settings['shutter_cycle'] = 0.1

    # # EIGER2 XE 9M
    # exposure_settings['det_args'] =  {'use_tiff_writer': False,
    #     'use_file_writer': True, 'photon_energy' : 12.0,
    #     'images_per_file': 100} #1 image/file for TR, 300 for eq SAXS, 1000 for muscle

    # Muscle settings
    exposure_settings['struck_measurement_time'] = '0.001'
    exposure_settings['tr_muscle_exp'] = False
    exposure_settings['open_shutter_before_trig_cont_exp'] = False

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
        # 'scale': 2.5e6, 'offset': 0, 'dark': True, 'norm_time': True},
        # {'mx_record': 'mcs12', 'channel': 11, 'name': 'Length_Out',
        # 'scale': 10e6, 'offset': 0, 'dark': False, 'norm_time': True},
        # {'mx_record': 'mcs13', 'channel': 12, 'name': 'Force',
        # 'scale': 10e6, 'offset': 0, 'dark': False, 'norm_time': True},
        # {'mx_record': 'mcs14', 'channel': 13, 'name': 'Length',
        # 'scale': 10e6, 'offset': 0, 'dark': False, 'norm_time': True},
        ]
    exposure_settings['warnings'] = {'shutter' : True, 'col_vac' : {'check': True,
        'thresh': 0.04}, 'guard_vac' : {'check': True, 'thresh': 0.04},
        'sample_vac': {'check': False, 'thresh': 0.04}, 'sc_vac':
        {'check': True, 'thresh':0.04}}
    exposure_settings['base_data_dir'] = '/nas_data/Pilatus1M/2026_1M/2026_Run1/' #CHANGE ME and pipeline local_basedir
    exposure_settings['data_dir'] = exposure_settings['base_data_dir']


    ###################################################################
    # Coflow settings
    coflow_settings = coflowcon.default_coflow_settings


    ###################################################################
    # TR-SAXS settings
    trsaxs_settings = trcon.default_trsaxs_settings


    ###################################################################
    # Scan Settings
    scan_settings = {
        'components'            : ['scan'],
        'newport_ip'            : '164.54.204.76',
        'newport_port'          : '5001',
        'show_advanced_options' : True,
        'motor_group_name'      : 'XY',
        }


    ###################################################################
    # Metadata Settings
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


    ###################################################################
    # Pipeline Settings
    pipeline_settings = {
        'components'    : ['pipeline'],
        'output_basedir': '/nas_data/SAXS',
        'server_port'   : '5556',
        'server_ip'     : '164.54.204.142', #EPU
        # 'server_ip'     : '164.54.204.144', #Marvin

        # EIGER settings
        # 'local_basedir' : '/nas_data/Eiger2x',
        # 'data_basedir'  : '/nas_data/Eiger2x',
        # 'data_source'   : 'Stream', #File or stream
        # 'detector'      : 'Eiger',

        # Pilatus settings
        'local_basedir' : '/nas_data/Pilatus1M/2026_1M',
        'data_basedir'  : '/nas_data/Pilatus1M/2026_1M',
        'data_source'   : 'File', #File or stream
        'detector'      : 'Pilatus',
        }


    ###################################################################
    # UV Settings
    spectrometer_settings = spectrometercon.default_spectrometer_settings
    spectrometer_settings['inline_panel'] = True
    spectrometer_settings['device_communication'] = 'remote'
    spectrometer_settings['remote_dir_prefix'] = {'local' : '/nas_data', 'remote' : 'Z:\\'}


    ###################################################################
    # HPLC Settings
    hplc_settings = biohplccon.default_hplc_2pump_settings
    hplc_settings['com_thread'] = None
    hplc_settings['remote'] = True
    hplc_settings['remote_device'] = 'hplc'
    hplc_settings['remote_ip'] = '164.54.204.113'
    hplc_settings['remote_port'] = '5556'
    hplc_settings['device_data'] = hplc_settings['device_init'][0]


    ###################################################################
    # Automator Settings
    automator_settings = autocon.default_automator_settings

    ###################################################################
    # Autosampler Settings
    autosampler_settings = autosamplercon.default_autosampler_settings
    autosampler_settings['com_thread'] = None
    autosampler_settings['device_communication'] = 'remote'
    autosampler_settings['remote'] = True
    autosampler_settings['remote_device'] = 'autosampler'
    autosampler_settings['remote_ip'] = '164.54.204.53'
    autosampler_settings['remote_port'] = '5557'
    autosampler_settings['device_data'] = autosampler_settings['device_init'][0]
    autosampler_settings['inline_panel'] = True

    ###################################################################
    # Toaster Settings
    toaster_settings = toastcon.default_toaster_settings

    ###################################################################
    # Mono Auto Tune Settings
    mono_auto_tune_settings = monotunecon.default_mono_tune_settings

    biocon_settings = {}

    components = OrderedDict([
        ('exposure', expcon.ExpPanel),
        ('coflow', coflowcon.CoflowPanel),
        # ('trsaxs_scan', trcon.TRScanPanel),
        # ('trsaxs_flow', trcon.TRFlowPanel),
        # ('scan',    scancon.ScanPanel),
        ('metadata', metadata.ParamPanel),
        ('pipeline', pipeline_ctrl.PipelineControl),
        ('uv', spectrometercon.UVPanel),
        ('hplc', biohplccon.HPLCPanel),
        ('automator', autocon.AutoPanel),
        ('autosampler', autosamplercon.AutosamplerPanel),
        # ('toaster', toastcon.ToasterPanel),
        # ('mono_auto_tune', monotunecon.MonoAutoTune)
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
        'autosampler'   : autosampler_settings,
        'toaster'       : toaster_settings,
        'mono_auto_tune': mono_auto_tune_settings,
        'components'    : components,
        'biocon'        : biocon_settings,
        }


    keys = list(settings['components'].keys())
    keys.append('biocon')

    for key in settings:
        if key != 'components' and key != 'biocon':
            settings[key]['components'] = keys

    app = wx.App()

    standard_paths = wx.StandardPaths.Get() #Can't do this until you start the wx app
    info_dir = standard_paths.GetUserLocalDataDir()

    os.makedirs(info_dir, exist_ok=True)

    h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'biocon.log'),
        maxBytes=int(10e6), backupCount=5, delay=True)
    h2.setLevel(logging.DEBUG)
    formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    h2.setFormatter(formatter2)

    logger.addHandler(h2)

    logger.debug('Setting up wx app')
    frame = BioFrame(settings, None, title='BioCAT Control', name='biocon')
    frame.Show()
    app.MainLoop()


