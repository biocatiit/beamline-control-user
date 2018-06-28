import matplotlib
matplotlib.use('WXAgg')
import wx
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as FigureCanvas
from matplotlib.backends.backend_wxagg import NavigationToolbar2WxAgg
from matplotlib.figure import Figure
from ..utils import Plotter
import numpy as np

class plot_gui(wx.Frame):
    """
    GUI for plotting maps. This will display matplotlib figure with Navigation Toolbar
    """
    def __init__(self, motor_x, motor_y, formula, xlim=None, ylim=None):
        super(plot_gui, self).__init__(None, title='MX Map Plot', name='plot')
        self.motor_x = motor_x
        self.motor_y = motor_y
        self.formula = formula
        self.initUI()
        self.xlim = xlim
        self.ylim = ylim
        self.setConnections()
        self.flipX = False
        self.flipY = True
        self.swap_xy = False
        self.x = self.y = self.z = None
        self.manualIntensity = False
        self.plotting = False
        x_step = y_step = None
        if xlim is not None:
            x_step = xlim[2]
        if ylim is not None:
            y_step = ylim[2]
        self.plotter = Plotter(self.motor_x, self.motor_y, formula, x_step, y_step)

    def initUI(self):
        """
        Initial all gui
        """
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        self.panel = wx.Panel(self)

        self.panel_sizer = wx.BoxSizer(wx.VERTICAL)
        self.panel_grid_sizer = wx.GridBagSizer(10, 10)

        # Add buttons and colormap options
        # Add Flip X
        self.flip_x = wx.Button(self.panel, wx.ID_ANY, "Flip X")
        self.panel_grid_sizer.Add(self.flip_x, pos=(0, 0), span=(1, 1), flag=wx.EXPAND)

        # Add Flip Y
        self.flip_y = wx.Button(self.panel, wx.ID_ANY, "Flip Y")
        self.panel_grid_sizer.Add(self.flip_y, pos=(1, 0), span=(1, 1), flag=wx.EXPAND)

        # Full Zoom out
        self.swap_xy_btn = wx.Button(self.panel, wx.ID_ANY, "Swap XY")
        self.panel_grid_sizer.Add(self.swap_xy_btn, pos=(2, 0), span=(1, 1), flag=wx.EXPAND)

        # Add color map options
        self.panel_grid_sizer.Add(wx.StaticText(self.panel, label='Colormap :'), pos=(0, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT)
        colormaps = sorted([str(m) for m in matplotlib.cm.datad if not m.endswith("_r")], key=str.lower)
        self.colors = wx.Choice(self.panel, -1, choices=colormaps)

        try:
            scan_window = wx.FindWindowByName('scan')
            self.colors.SetStringSelection(scan_window.default_plt_color)
        except Exception:
            self.colors.SetStringSelection('jet')
        self.panel_grid_sizer.Add(self.colors, pos=(0, 2), span=(1, 2), flag=wx.EXPAND)

        # Add Min & Max intensities
        self.panel_grid_sizer.Add(wx.StaticText(self.panel, label='Min Intensity :'), pos=(1, 1), span=(1, 1),
                             flag=wx.EXPAND | wx.LEFT)
        self.panel_grid_sizer.Add(wx.StaticText(self.panel, label='Max Intensity :'), pos=(2, 1), span=(1, 1),
                             flag=wx.EXPAND | wx.LEFT)
        self.minInt = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS | wx.TE_PROCESS_ENTER, min=0, max=100, initial=0)
        self.minInt.SetDigits(3)
        self.panel_grid_sizer.Add(self.minInt, pos=(1, 2), span=(1, 1), flag=wx.EXPAND)
        self.maxInt = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS | wx.TE_PROCESS_ENTER, min=0, max=100, initial=100)
        self.maxInt.SetDigits(3)
        self.panel_grid_sizer.Add(self.maxInt, pos=(2, 2), span=(1, 1), flag=wx.EXPAND)
        self.panel_grid_sizer.Add(wx.StaticText(self.panel, label='%'), pos=(1, 3), span=(1, 1))
        self.panel_grid_sizer.Add(wx.StaticText(self.panel, label='%'), pos=(2, 3), span=(1, 1))

        self.click_pos = wx.StaticText(self.panel, label='')

        self.panel_grid_sizer.Add(wx.StaticText(self.panel, label='Selected Position:'), pos=(3,0))
        self.panel_grid_sizer.Add(self.click_pos, pos=(3,1), span=(1,4))

        # Add Figure
        self.figure = Figure()
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvas(self.panel, -1, self.figure)

        self.axes.set_xlabel(self.motor_x)
        self.axes.set_ylabel(self.motor_y)
        self.axes.set_title(str(self.formula))


        # Add toolbar
        self.toolbar = CustomPlotToolbar(self.panel, self.canvas)
        self.toolbar.Realize()
        self.toolbar.update()

        plot_sizer = wx.BoxSizer(wx.VERTICAL)
        plot_sizer.Add(self.canvas, 1, flag=wx.EXPAND)
        plot_sizer.Add(self.toolbar, 0, flag=wx.EXPAND)

        self.panel_sizer.Add(self.panel_grid_sizer, border=5, flag=wx.EXPAND|wx.ALL)
        self.panel_sizer.Add(plot_sizer, 1, flag=wx.EXPAND)


        self.panel.SetSizer(self.panel_sizer)
        self.main_sizer.Add(self.panel, 1, flag=wx.EXPAND)

        self.SetSizer(self.main_sizer)
        self.SetAutoLayout(True)
        self.main_sizer.Fit(self)

    def setConnections(self):
        """
        Set Event Handlers
        """
        self.canvas.mpl_connect('button_press_event', self.onClicked)
        self.canvas.mpl_connect('motion_notify_event', self._on_mousemotion)
        self.Bind(wx.EVT_BUTTON, self.flipPlotX, self.flip_x)
        self.Bind(wx.EVT_BUTTON, self.flipPlotY, self.flip_y)
        self.Bind(wx.EVT_BUTTON, self.on_swap_xy, self.swap_xy_btn)
        self.Bind(wx.EVT_CHOICE, self.update_color, self.colors)
        self.Bind(wx.EVT_TEXT, self.update_plot, self.minInt)
        self.Bind(wx.EVT_TEXT, self.update_plot, self.maxInt)
        self.Bind(wx.EVT_SPINCTRL, self.update_plot, self.minInt)
        self.Bind(wx.EVT_SPINCTRL, self.update_plot, self.maxInt)
        self.Bind(wx.EVT_TEXT_ENTER, self.update_plot, self.minInt)
        self.Bind(wx.EVT_TEXT_ENTER, self.update_plot, self.maxInt)

    def flipPlotX(self, e):
        """
        Triggered when "Flip X" is clicked. Flip plot in X direction
        :return:
        """
        self.flipX = not self.flipX
        self.axes.invert_xaxis()
        self.canvas.draw()

    def flipPlotY(self, e):
        """
        Triggered when "Flip Y" is clicked. Flip plot in Y direction
        :return:
        """
        self.flipY = not self.flipY
        self.axes.invert_yaxis()
        self.canvas.draw()

    def update_color(self, event):
        try:
            scan_window = wx.FindWindowByName('scan')
            scan_window.default_plt_color = str(self.colors.GetStringSelection())
        except Exception:
            pass

        self.update_plot()

    def on_swap_xy(self, event):
        self.swap_xy = not self.swap_xy

        xlabel = self.axes.get_ylabel()
        ylabel = self.axes.get_xlabel()

        self.axes.set_xlabel(xlabel)
        self.axes.set_ylabel(ylabel)

        ylim = self.xlim
        xlim = self.ylim

        self.xlim = xlim
        self.ylim = ylim

        self.update_plot()

    def plot(self, output):
        """
        Triggered from other thread to read data from output file and plot
        :param output: full path of output file
        """
        self.plotting = True
        wx.CallAfter(self.read_and_plot, output)
        # self.read_and_plot(output)

    def read_and_plot(self, output):
        """
        Read output and plot to panel
        :param output: full path of output file
        :return:
        """
        print("Plot : %s" % (output))

        # Read and update plot
        if self.plotter.read(output):
            self.update_plot()

        self.plotting = False


    def update_plot(self, e=None):
        """
        Get x,y,z from Plotter and plot
        :return:
        """
        if not self.swap_xy:
            self.x, self.y, self.z = self.plotter.getXYZ()
        else:
            self.y, self.x, self.z = self.plotter.getXYZ()

        if self.x is not None:
            z = np.copy(self.z)
            minInt = self.minInt.GetValue()
            maxInt = self.maxInt.GetValue()
            if minInt != 0 or maxInt != 0:
                min_val = z.min()
                max_val = z.max()
                ran = max_val - min_val
                minInt = min_val + ran * minInt / 100.
                maxInt = min_val + ran * maxInt / 100.
                z[z > maxInt] = maxInt
                z[z < minInt] = minInt
                z -= min_val

            xlabel = self.axes.get_xlabel()
            ylabel = self.axes.get_ylabel()
            title = self.axes.get_title()

            self.axes.cla()
            self.axes.pcolormesh(self.x, self.y, z, cmap=str(self.colors.GetStringSelection()))

            self.axes.set_xlabel(xlabel)
            self.axes.set_ylabel(ylabel)
            self.axes.set_title(title)

            # Set x, y limits if they're available
            if self.xlim is not None:
                lim = (self.xlim[0], self.xlim[1]+self.xlim[2])
                self.axes.set_xlim(lim)
            if self.ylim is not None:
                lim = (self.ylim[0], self.ylim[1] + self.ylim[2])
                self.axes.set_ylim(lim)

            if self.flipX:
                self.axes.invert_xaxis()
            if self.flipY:
                self.axes.invert_yaxis()

            self.canvas.draw()

    def onClicked(self, e):
        """
        Handle when the plot is clicked
        """
        if self.x is None or e.xdata is None or e.ydata is None:
            return

        x = e.xdata
        y = e.ydata

        all_xs = np.arange(len(self.x[0, :]))
        ind_x = min(all_xs, key=lambda i: abs(x-self.x[0][i]))

        all_ys = np.arange(len(self.y[:, 0]))
        ind_y = min(all_ys, key=lambda i: abs(y-self.y[i][0]))

        if self.x[0,ind_x] > x:
            ind_x -= 1
        if self.y[ind_y, 0] > y:
            ind_y -= 1

        # Print x, y coordinates and intensity z
        if ind_y < self.z.shape[0] and ind_x < self.z.shape[1]:
            coord_info = 'x='+str(x)+', y='+str(y)+', z='+str(self.z[ind_y][ind_x])
            self.click_pos.SetLabel(coord_info)
            print(coord_info)

    def _on_mousemotion(self, event):
        if event.inaxes:
            x = event.xdata
            y = event.ydata
            try:
                all_xs = np.arange(len(self.x[0, :]))
                ind_x = min(all_xs, key=lambda i: abs(x-self.x[0][i]))

                all_ys = np.arange(len(self.y[:, 0]))
                ind_y = min(all_ys, key=lambda i: abs(y-self.y[i][0]))

                if self.x[0,ind_x] > x:
                    ind_x -= 1
                if self.y[ind_y, 0] > y:
                    ind_y -= 1

                if ind_y < self.z.shape[0] and ind_x < self.z.shape[1]:
                    z = self.z[ind_y][ind_x]
                else:
                    z = ''
            except TypeError:
                z = ''

            self.toolbar.set_status('x={}, y={}, z={}'.format(x, y, z))
        else:
            self.toolbar.set_status('')

class CustomPlotToolbar(NavigationToolbar2WxAgg):
    def __init__(self, parent, canvas):
        NavigationToolbar2WxAgg.__init__(self, canvas)

        self.status = wx.StaticText(self, label='')

        self.AddControl(self.status)

    def set_status(self, status):
        self.status.SetLabel(status)
