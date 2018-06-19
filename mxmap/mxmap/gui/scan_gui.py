import os
from os.path import exists, join, split
import threading
import multiprocessing
import time
import queue

import wx
import wx.lib.scrolledpanel as scrolled



from ..utils.Scanner import Scanner
from ..utils.formula import calculate
from ..utils import mputils
from plot_gui import plot_gui

try:
    import configparser as CP
except ImportError:
    import ConfigParser as CP


class scan_gui(wx.Frame):
    """
    GUI for scanner program. This program allows user to set start, end, and step size for motor x and y. Also, number of scalers and plot formula. (Detectors are not supported now)
    """
    def __init__(self, title):
        super(scan_gui, self).__init__(None, title=title)
        self.all_scalers = []
        self.xmotor_list = ['None']
        self.ymotor_list = ['None']
        self.scaler_list = ['None']
        self.detector_list = ['None']

        self.manager = multiprocessing.Manager()
        self.mx_cmd_q = self.manager.Queue()
        self.mx_return_q = self.manager.Queue()
        self.mx_abort_event = self.manager.Event()
        self.scanner = Scanner(self.mx_cmd_q, self.mx_return_q, self.mx_abort_event)
        self.scanner.start()

        self.double_digits = 4
        self.readConfigs()
        self.initUI()
        self.setConnections()
        self.Show()
        # self.SetSizeHints((840, 400))
        # self.SetSize((840, 400))

    def readConfigs(self):
        """
        Read Configuration file and set database list, scaler fields, and detector fields
        """
        config_path = "/etc/mxmap_config.ini"
        if not exists(config_path):
            print("WARNING : {} does not exists. Default configuration will be used instead.".format(config_path))
            path, name = split(__file__)
            config_path = join(path, 'mxmap_config.ini')
        # print(config_path)
        config = CP.ConfigParser()
        config.read(config_path)
        self.db_list = ['Please select MX Database']

        # Get available database list
        if config.has_option('mx', 'DATABASE'):
            dbs = config.get('mx','DATABASE').split(',')
            for db in dbs:
                if db not in self.db_list and os.path.exists(db):
                    self.db_list.append(db)
        else:
            # Default
            self.db_list.extend(['/opt/mx/etc/mvortex.dat','/etc/mx/mxmotor.dat'])

        # Get Scaler mx_class names
        if config.has_option('mx', 'SCALER_CLASSES'):
            self.scaler_fields = config.get('mx','SCALER_CLASSES').split(',')
        else:
            # Default
            self.scaler_fields = ['scaler','mca_value']

        # Get Detector mx_class name
        if config.has_option('mx', 'DETECTOR_CLASSES'):
            self.det_fields = config.get('mx','DETECTOR_CLASSES').split(',')
        else:
            # Default
            self.det_fields = ['mca','area_detector']

        # Get Timer
        if config.has_option('mx', 'TIMERS'):
            self.timer = config.get('mx','TIMERS')
        else:
            # Default
            self.timer = 'joerger_timer'


        if "MXDATBASE" in os.environ:
            database_filename = os.environ["MXDATABASE"]
            if database_filename not in self.db_list and os.path.exists(database_filename):
                self.db_list.append(database_filename)

        mxdir = mputils.get_mxdir()
        database_filename = os.path.join(mxdir, "etc", "mxmotor.dat")
        database_filename = os.path.normpath(database_filename)
        if database_filename not in self.db_list and os.path.exists(database_filename):
            self.db_list.append(database_filename)

    def getDevices(self):
        """
        Get list of devices from MX Database
        :return:
        """
        self.mx_cmd_q.put_nowait(['get_devices', [self.scaler_fields, self.det_fields], {}])
        response = None
        while response is None:
            try:
                response = self.mx_return_q.get_nowait()
            except queue.Empty:
                pass
            time.sleep(.001)

        self.xmotor_list, self.ymotor_list, self.scaler_list, self.detector_list = response

    def initUI(self):
        """
        Initial all ui
        """
        self.main_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.panel = wx.Panel(self)
        self.panel_sizer = wx.GridBagSizer(10, 10)

        # Add MX DB Path
        self.db_picker = wx.ComboBox(self.panel, -1, choices=self.db_list, style=wx.CB_READONLY)
        self.db_picker.SetSelection(0)
        self.panel_sizer.Add(wx.StaticText(self.panel, label='MX Database:'), pos=(0, 0), span=(1, 1),
                             flag=wx.ALIGN_CENTER_VERTICAL)
        self.panel_sizer.Add(self.db_picker, pos=(0, 1), span=(1, 2), flag=wx.EXPAND)

        # Add Motors Settings
        motors_box = wx.StaticBox(self.panel, wx.ID_ANY, "Motors")
        motor_sizer = self.initMotorSizer(motors_box)
        self.panel_sizer.Add(motor_sizer, pos=(1,0), span=(1, 2))

        # Add scalers Settings
        scaler_sizer = self.initscalerpanel()
        # self.panel_sizer.Add(scaler_sizer, pos=(1, 2), span=(2, 1),  flag=wx.EXPAND)

        # Add Detectors Settings
        detector_box = wx.StaticBox(self.panel, wx.ID_ANY, "Detector")
        detector_sizer = self.initDetectorSizer(detector_box)
        self.panel_sizer.Add(detector_sizer, pos=(2, 0), span=(1, 2), flag=wx.EXPAND)

        # Add directory field
        self.dir_picker = wx.DirPickerCtrl(self.panel, wx.ID_ANY, path=os.getcwd(), message="Select an output directory")
        self.panel_sizer.Add(wx.StaticText(self.panel, label='Output directory:'), pos=(3, 0), span=(1, 1), flag=wx.ALIGN_CENTER_VERTICAL)
        self.panel_sizer.Add(self.dir_picker, pos=(3, 1), span=(1, 2), flag=wx.EXPAND)
        self.panel_sizer.Add(wx.StaticText(self.panel, label='Output Template:'), pos=(4, 0), span=(1, 1),
                             flag=wx.ALIGN_CENTER_VERTICAL)
        self.filename = wx.TextCtrl(self.panel)
        self.filename.SetHint("Enter output name")
        self.panel_sizer.Add(self.filename, pos=(4, 1), span=(1, 2), flag=wx.EXPAND)

        # Add start and stop buttons
        self.start_button = wx.Button(self.panel, wx.ID_ANY, "Start")
        self.stop_button = wx.Button(self.panel, label="Stop after current row")
        self.stop_button.Disable()
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sizer.Add(self.start_button, border=5, flag=wx.BOTTOM)
        btn_sizer.Add(self.stop_button, border=5, flag=wx.BOTTOM|wx.LEFT)
        self.panel_sizer.Add(btn_sizer, pos=(5, 0), span=(1, 3), flag=wx.ALIGN_CENTER)

        self.panel.SetSizer(self.panel_sizer)

        self.main_sizer.Add(self.panel, 2, border=5, flag=wx.GROW|wx.ALL)
        self.main_sizer.Add(scaler_sizer, 1, border=5, flag=wx.EXPAND|wx.TOP|wx.RIGHT|wx.BOTTOM)

        self.statusbar = self.CreateStatusBar(1)

        self.SetSizer(self.main_sizer)
        self.SetAutoLayout(True)
        self.main_sizer.Fit(self)

    def initMotorSizer(self, box):
        """
        Generate Motor Sizer contains motor X and Y settings
        :param box: Boxsizer
        :return:
        """
        motor_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        grid_sizer = wx.GridBagSizer(5, 5)

        self.motorx_name = wx.ComboBox(self.panel, -1, choices=self.xmotor_list, style=wx.CB_READONLY)
        self.motorx_name.SetSelection(0)
        self.motorx_start = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=-10000000, max=10000000, initial=0)
        self.motorx_start.SetDigits(self.double_digits)
        self.motorx_end = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=-10000000, max=10000000, initial=500)
        self.motorx_end.SetDigits(self.double_digits)
        self.motorx_step = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=1, max=10000000, initial=100)
        self.motorx_step.SetDigits(self.double_digits)

        self.motory_name = wx.ComboBox(self.panel, -1, choices=self.ymotor_list, style=wx.CB_READONLY)
        self.motory_name.SetSelection(0)
        self.motory_start = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=-10000000, max=10000000, initial=0)
        self.motory_start.SetDigits(self.double_digits)
        self.motory_end = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=-10000000, max=10000000, initial=400)
        self.motory_end.SetDigits(self.double_digits)
        self.motory_step = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS, min=1, max=10000000, initial=100)
        self.motory_step.SetDigits(self.double_digits)

        # Add X
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Motor X:"), pos=(0, 0), span=(1, 1))
        grid_sizer.Add(self.motorx_name, pos=(0, 1), span=(1, 5))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Start:"), pos=(1, 0), span=(1, 1))
        grid_sizer.Add(self.motorx_start, pos=(1, 1), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="End:"), pos=(1, 2), span=(1, 1))
        grid_sizer.Add(self.motorx_end, pos=(1, 3), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Step size:"), pos=(1, 4), span=(1, 1))
        grid_sizer.Add(self.motorx_step, pos=(1, 5), span=(1, 1))

        separator = wx.StaticLine(self.panel, -1, style=wx.LI_HORIZONTAL)
        grid_sizer.Add(separator, pos=(2, 0), span=(1, 6), flag=wx.GROW|wx.ALIGN_CENTER_VERTICAL|wx.ALL)

        # Add Y
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Motor Y:"), pos=(3, 0), span=(1, 1))
        grid_sizer.Add(self.motory_name, pos=(3, 1), span=(1, 5))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Start:"), pos=(4, 0), span=(1, 1))
        grid_sizer.Add(self.motory_start, pos=(4, 1), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="End:"), pos=(4, 2), span=(1, 1))
        grid_sizer.Add(self.motory_end, pos=(4, 3), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Step size:"), pos=(4, 4), span=(1, 1))
        grid_sizer.Add(self.motory_step, pos=(4, 5), span=(1, 1))

        motor_sizer.Add(grid_sizer)
        return motor_sizer

    def initscalerpanel(self):
        """
        Generate scaler Sizer contains scaler settings
        :param box: Boxsizer
        :return:
        """
        # Scrolled panel stuff
        self.scrolled_panel = scrolled.ScrolledPanel(self, -1,
                                                     style=wx.TAB_TRAVERSAL | wx.SUNKEN_BORDER)
        self.scrolled_panel.SetAutoLayout(1)
        self.scrolled_panel.SetupScrolling()
        scaler_box = wx.StaticBox(self.scrolled_panel, wx.ID_ANY, "scalers")
        scaler_sizer = wx.StaticBoxSizer(scaler_box, wx.VERTICAL)

        grid_sizer = wx.GridBagSizer(5, 5)
        self.numscalers = wx.SpinCtrl(parent=self.scrolled_panel, style=wx.SP_ARROW_KEYS|wx.TE_PROCESS_ENTER, min=1, max=100, initial=1)
        self.numscalers.SetValue(1) #Shouldn't be necessary, but is on my mac?
        self.dwell_time = wx.SpinCtrlDouble(parent=self.scrolled_panel, style=wx.SP_ARROW_KEYS|wx.TE_PROCESS_ENTER, min=0.000000001, max=1000000000, initial=0.5, inc=0.5)
        self.dwell_time.SetDigits(self.double_digits)
        self.scaler_list_sizer = wx.BoxSizer(wx.VERTICAL)
        self.formula = wx.TextCtrl(parent=self.scrolled_panel, value="")

        grid_sizer.Add(wx.StaticText(parent=self.scrolled_panel, label="Number of scalers:"), pos=(0, 0), span=(1,2))
        grid_sizer.Add(self.numscalers, pos=(0, 2), span=(1, 1))
        grid_sizer.Add(wx.StaticText(parent=self.scrolled_panel, label="Dwell time:"), pos=(1, 0), span=(1,1))
        grid_sizer.Add(self.dwell_time, pos=(1, 2), span=(1, 1))
        grid_sizer.Add(self.scaler_list_sizer, pos=(2, 0), span=(1, 3))
        grid_sizer.Add(wx.StaticText(parent=self.scrolled_panel, label="Plot Formula:"), pos=(3, 0), span=(1, 1))
        grid_sizer.Add(self.formula, pos=(3, 1), span=(1,2))
        scaler_sizer.Add(grid_sizer)

        self.scrolled_panel.SetSizer(scaler_sizer)

        self.refreshscaler(None)

        return self.scrolled_panel

    def initDetectorSizer(self, box):
        """
        Generate Detector Sizer contains Detector settings
        :param box: Boxsizer
        :return:
        """
        det_sizer = wx.StaticBoxSizer(box, wx.VERTICAL)
        grid_sizer = wx.GridBagSizer(5, 5)
        self.detector = wx.ComboBox(self.panel, -1, choices=['None'], style=wx.CB_READONLY)
        self.detector.SetSelection(0)

        grid_sizer.Add(wx.StaticText(parent=self.panel, label="Detector:"), pos=(0, 0), span=(1,2))
        grid_sizer.Add(self.detector, pos=(0, 2), span=(1, 5), flag = wx.EXPAND)

        det_sizer.Add(grid_sizer)

        return det_sizer

    def setConnections(self):
        """
        Set Handlers to widget events
        """
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.Bind(wx.EVT_COMBOBOX, self.DBPathSelected, self.db_picker)
        self.Bind(wx.EVT_SIZE, self.OnSize)
        self.Bind(wx.EVT_TEXT, self.refreshscaler, self.numscalers)
        self.Bind(wx.EVT_SPINCTRL, self.refreshscaler, self.numscalers)
        self.Bind(wx.EVT_TEXT_ENTER, self.refreshscaler, self.numscalers)
        self.Bind(wx.EVT_COMBOBOX, self.detectorChanged, self.detector)
        self.Bind(wx.EVT_BUTTON, self.startPressed, self.start_button)

        self.stop_button.Bind(wx.EVT_BUTTON, self._on_stop)

    def OnClose(self, e):
        self.scanner.stop()
        self.Destroy()

    def DBPathSelected(self, e):
        """
        Handle when DB Path is changed. MX DB can be set only once so the combobox will be disabled
        """
        picked = str(self.db_picker.GetValue())
        if picked == 'Please select MX Database':
            return

        if not exists(picked):
            print("Error : %s does not exist"%(picked))
            self.db_picker.SetSelection(0)
            return

        # Set DB Path for Scanner
        self.mx_cmd_q.put_nowait(['start_mxdb', [picked], {}])

        # Disable DB Path picker
        self.db_picker.Disable()

        # Get Device list
        self.getDevices()

        # Refresh Motor X choices
        self.motorx_name.Clear()
        for mx in self.xmotor_list:
            self.motorx_name.Append(mx)

        # set motor x to smx
        if 'smx' in self.xmotor_list:
            self.motorx_name.SetValue('smx')
        else:
            self.motorx_name.SetSelection(0)

        # Refresh Motor Y choices
        self.motory_name.Clear()
        for my in self.ymotor_list:
            self.motory_name.Append(my)
        self.motory_name.SetSelection(0)

        # set motor y to smy
        if 'smy' in self.ymotor_list:
            self.motory_name.SetValue('smy')
        else:
            self.motory_name.SetSelection(0)

        # Refresh Scaler choices
        for scaler in self.all_scalers:
            scaler.Clear()
            for s in self.scaler_list:
                scaler.Append(s)

            # Set scaler to Io
            if 'Io' in self.scaler_list:
                scaler.SetValue('Io')
            else:
                scaler.SetSelection(0)

        # Refresh Detector choices
        self.detector.Clear()
        self.detector.Append('None')
        for d in self.detector_list:
            self.detector.Append(d)
        self.detector.SetSelection(0)

        # Init Formula
        if len(self.scaler_list) > 0:
            self.formula.SetLabelText(self.scaler_list[0])
        else:
            self.formula.SetLabelText('')

        self.statusbar.SetStatusText('Status: Ready to scan')

    def refreshscaler(self, e):
        """
        Handle when number of scalar is changed
        """
        current = len(self.all_scalers)
        expected = self.numscalers.GetValue()

        if current < expected:
            # Add scaler if expected number of scalers is higher than current number of scalers
            for i in range(expected - current):
                scaler_items = wx.BoxSizer(wx.HORIZONTAL)
                scaler = wx.ComboBox(self.scrolled_panel, -1, choices=self.scaler_list, style=wx.CB_READONLY)

                # initial as Io if it exists
                if 'Io' in self.scaler_list:
                    scaler.SetValue('Io')
                else:
                    scaler.SetSelection(0)

                scaler_items.Add(wx.StaticText(self.scrolled_panel, label=str(current+i+1)+'. '))
                scaler_items.Add(scaler, flag = wx.EXPAND)
                self.all_scalers.append(scaler)
                self.scaler_list_sizer.Add(scaler_items)
                self.main_sizer.Layout()
                self.main_sizer.Fit(self)

        elif current > expected:
            # Remove scalers from bottom if expected number of scaler is less than current scalers
            for i in range(current-expected):
                self.all_scalers.pop()
                self.scaler_list_sizer.Hide(current - i - 1)
                self.scaler_list_sizer.Remove(current-i-1)
                self.main_sizer.Layout()
                self.main_sizer.Fit(self)

        self.scrolled_panel.FitInside()
        self.scrolled_panel.Layout()
        self.scrolled_panel.SetupScrolling()

    def detectorChanged(self, e):
        """
        Handle when detector is changed
        """
        print("Current detector is "+str(self.detector.GetValue()))

    def startPressed(self, e):
        """
        Handle when start button is pressed
        """
        if self.checkSettings():
            self.statusbar.SetStatusText('Status: Scanning')
            self.start_button.Disable()
            self.stop_button.Enable()
            scalers = [str(s.GetValue()) for s in self.all_scalers]
            if self.detector.GetValue() == 'None':
                detector = None
            else:
                detector = {
                    'name' : str(self.detector.GetValue())
                }

            path = str(self.dir_picker.GetPath())
            file_name = str(self.filename.GetValue())
            dir_path = os.path.join(path, file_name)

            if not os.path.exists(dir_path):
                os.mkdir(dir_path)

                while not os.path.exists(dir_path):
                    time.sleep(.001)

            params = {
                'dir_path' : dir_path,
                'file_name' : file_name,
                'x_motor' : str(self.motorx_name.GetValue()),
                'x_start' : self.motorx_start.GetValue(),
                'x_step' : self.motorx_step.GetValue(),
                'x_end' : self.motorx_end.GetValue(),
                'y_motor' : str(self.motory_name.GetValue()),
                'y_start' : self.motory_start.GetValue(),
                'y_step' : self.motory_step.GetValue(),
                'y_end' : self.motory_end.GetValue(),
                'scalers' : scalers,
                'dwell_time' : self.dwell_time.GetValue(),
                'detector' : detector,
                'timer' : self.timer
            }

            self.plot_panel = plot_gui(motor_x=params['x_motor'], motor_y=params['y_motor'],
                formula=self.formula.GetValue(), xlim=(params['x_start'],
                    params['x_end'], params['x_step']), ylim=(params['y_start'],
                    params['y_end'], params['y_step']))
            self.plot_panel.Show(True)

            wx.Yield()

            # params['callback'] = plot_panel
            # params['main_win'] = self

            self.mx_cmd_q.put_nowait(['set_devices', [], params])
            # self.scanner.setDevices(**params)

            self.mx_cmd_q.put_nowait(['scan', [], {}])
            # self.scanner.runCommand('scan')
            print("Running")
            plot_thread = threading.Thread(target=self.update_plot)
            plot_thread.daemon = True
            plot_thread.start()

    def update_plot(self):
        while True:
            try:
                datafile_name = self.mx_return_q.get_nowait()[0]
            except queue.Empty:
                datafile_name = None

            if datafile_name is not None and datafile_name != 'stop_live_plotting':
                print(datafile_name)
                self.plot_panel.plot(datafile_name)
                wx.Yield()
            elif datafile_name == 'stop_live_plotting':
                break
            time.sleep(.01)

        self.scan_done()

    def checkSettings(self):
        """
        Check settings before running the scan
        :return:
        """
        # Check MX Database
        if str(self.db_picker.GetValue()) == 'Please select MX Database':
            print("Error : Please select MX Database")
            return False

        # Check scalers
        scalers = []
        d_scalers = {}
        for scaler in self.all_scalers:
            scalers.append(str(scaler.GetValue()))
            d_scalers[str(scaler.GetValue())] = 1.0

        if len(scalers) != len(set(scalers)):
            print("Error : 2 scalers have the same name")
            return False

        # Check fomula
        try:
            calculate(str(self.formula.GetValue()), d_scalers)
        except:
            print("Error : Invalid Formula. Please check if the formular is correct and all scalers are added")
            return False

        # Check output directory
        dir = self.dir_picker.GetPath()
        if not exists(dir):
            print("Error :",dir," does not exist. Please select another directory.")
            return False

        file_name = self.filename.GetValue()
        test_name = file_name.replace('_', '').replace('-', '')
        if not test_name.isalnum():
            print("Error : Invalid output file name. Please do not include space, '.' or any special characters")
            print("Current name : %s"%(file_name))
            return False

        return True

    def scan_done(self):
        """
        Trigger by scanner when all scans are done
        :return:
        """
        self.start_button.Enable()
        self.stop_button.Disable()

        #This is a hack
        self.scanner.stop()
        self.scanner = Scanner(self.mx_cmd_q, self.mx_return_q, self.mx_abort_event)
        self.scanner.start()
        picked = str(self.db_picker.GetValue())
        self.mx_cmd_q.put_nowait(['start_mxdb', [picked], {}])
        self.statusbar.SetStatusText('Status: Ready to scan')

    def OnSize(self, event):
        """
        Handler for window resizing - Do nothing now
        :param event:
        :return:
        """
        pass

    def _on_stop(self, event):
        self.statusbar.SetStatusText('Status: Stopping scan')
        self.mx_abort_event.set()
        self.stop_button.Disable()

def begin():
    app = wx.App()
    scan_gui('BMScan')
    app.MainLoop()
