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

if __name__ != '__main__':
    logger = logging.getLogger(__name__)

import wx

import expcon
import coflowcon

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
            box = wx.StaticBox(top_panel, label=key.capitalize())
            box.SetOwnForegroundColour(wx.Colour('firebrick'))
            component_panel = self.settings['components'][key](self.settings[key], box, name=key)

            component_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
            component_sizer.Add(component_panel, proportion=1, border=2,
                flag=wx.EXPAND|wx.ALL)

            self.component_sizers[key] = component_sizer
            self.component_panels[key] = component_panel

        if 'exposure' in self.component_sizers or 'coflow' in self.component_sizers:
            exp_sizer = wx.BoxSizer(wx.HORIZONTAL)

            if 'exposure' in self.component_sizers:
                exp_sizer.Add(self.component_sizers['exposure'], proportion=1, border=10,
                    flag=wx.EXPAND|wx.ALL)

            if 'coflow' in self.component_sizers:
                exp_sizer.Add(self.component_sizers['coflow'], border=10,
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
        'exp_period'            : '1.5',
        'exp_num'               : '5',
        'exp_time_min'          : 0.00105,
        'exp_time_max'          : 5184000,
        'exp_period_min'        : 0.002,
        'exp_period_max'        : 5184000,
        'nframes_max'           : 4000, # For Pilatus: 999999, for Struck: 4000 (set by maxChannels in the driver configuration)
        'exp_period_delta'      : 0.00095,
        'slow_mode_thres'       : 0.1,
        'fast_mode_max_exp_time': 2000,
        'wait_for_trig'         : False,
        'num_trig'              : '4',
        'show_advanced_options' : False,
        'fe_shutter_pv'         : 'FE:18:ID:FEshutter',
        'd_shutter_pv'          : 'PA:18ID:STA_D_SDS_OPEN_PL.VAL',
        'local_dir_root'        : '/nas_data/Pilatus1M',
        'remote_dir_root'       : '/nas_data',
        'base_data_dir'         : '/nas_data/Pilatus1M/20190122Hopkins', #CHANGE ME
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
        'outlet_pump'           : ('VICI_M50', 'COM6', [627.32, 11.826], {}),
        'sheath_fm'             : ('BFS', 'COM3', [], {}),
        'outlet_fm'             : ('BFS', 'COM4', [], {}),
        'sheath_ratio'          : 0.5,
        'sheath_excess'         : 2.1,
        'warning_threshold_low' : 0.8,
        'warning_threshold_high': 1.2,
        'settling_time'         : 5000, #in ms
        'lc_flow_rate'          : '0.8',
        }

    biocon_settings = {}

    components = {
        'exposure'  : expcon.ExpPanel,
        'coflow'    : coflowcon.CoflowPanel,
        }

    settings = {'coflow'    : coflow_settings,
        'exposure'          : exposure_settings,
        'components'        : components,
        'biocon'            : biocon_settings
        }


    for key in settings:
        if key != 'components' and key != 'biocon':
            settings[key]['components'] = settings['components'].keys()

    app = wx.App()

    # standard_paths = wx.StandardPaths.Get() #Can't do this until you start the wx app
    # info_dir = standard_paths.GetUserLocalDataDir()

    # if not os.path.exists(info_dir):
    #     os.mkdir(info_dir)
    # # if not os.path.exists(os.path.join(info_dir, 'expcon.log')):
    # #     open(os.path.join(info_dir, 'expcon.log'), 'w')
    # h2 = handlers.RotatingFileHandler(os.path.join(info_dir, 'expcon.log'), maxBytes=10e6, backupCount=5, delay=True)
    # h2.setLevel(logging.DEBUG)
    # formatter2 = logging.Formatter('%(asctime)s - %(name)s - %(threadName)s - %(levelname)s - %(message)s')
    # h2.setFormatter(formatter2)

    # logger.addHandler(h2)

    logger.debug('Setting up wx app')
    frame = BioFrame(settings, None, title='BioCAT Control')
    frame.Show()
    app.MainLoop()


