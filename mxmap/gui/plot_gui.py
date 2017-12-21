import matplotlib
matplotlib.use('WXAgg')
import wx
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as FigureCanvas
from matplotlib.backends.backend_wx import NavigationToolbar2Wx
from matplotlib.figure import Figure
from ..utils import Plotter
import numpy as np

class plot_gui(wx.Frame):
    """
    GUI for plotting maps. This will display matplotlib figure with Navigation Toolbar
    """
    def __init__(self, motor_x, motor_y, formula, xlim=None, ylim=None):
        super(plot_gui, self).__init__(None, title=str(formula))
        self.motor_x = motor_x
        self.motor_y = motor_y
        self.initUI()
        self.xlim = xlim
        self.ylim = ylim
        self.setConnections()
        self.flipX = False
        self.flipY = True
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
        self.panel_sizer = wx.GridBagSizer(10, 10)

        # Add buttons and colormap options
        # Add Flip X
        self.flip_x = wx.Button(self.panel, wx.ID_ANY, "Flip X")
        self.panel_sizer.Add(self.flip_x, pos=(0, 0), span=(1, 1), flag=wx.EXPAND)

        # Add Flip Y
        self.flip_y = wx.Button(self.panel, wx.ID_ANY, "Flip Y")
        self.panel_sizer.Add(self.flip_y, pos=(1, 0), span=(1, 1), flag=wx.EXPAND)

        # Full Zoom out
        self.full_button = wx.Button(self.panel, wx.ID_ANY, "Full Zoom Out")
        self.panel_sizer.Add(self.full_button, pos=(2, 0), span=(1, 1), flag=wx.EXPAND)

        # Add color map options
        self.panel_sizer.Add(wx.StaticText(self.panel, label='Colormap :'), pos=(0, 1), span=(1, 1), flag=wx.EXPAND|wx.LEFT)
        colormaps = ['jet', 'inferno', 'gray', 'gnuplot', 'gnuplot2', 'hsv', 'magma', 'ocean',
                     'rainbow', 'seismic', 'summer', 'spring', 'terrain', 'winter', 'autumn',
                     'Blues', 'Greens', 'Oranges', 'Reds', 'pink']
        self.colors = wx.ComboBox(self.panel, -1, choices=colormaps, style=wx.CB_READONLY)
        self.colors.SetValue('jet')
        self.panel_sizer.Add(self.colors, pos=(0, 2), span=(1, 2), flag=wx.EXPAND)

        # Add Min & Max intensities
        self.panel_sizer.Add(wx.StaticText(self.panel, label='Min Intensity :'), pos=(1, 1), span=(1, 1),
                             flag=wx.EXPAND | wx.LEFT)
        self.panel_sizer.Add(wx.StaticText(self.panel, label='Max Intensity :'), pos=(2, 1), span=(1, 1),
                             flag=wx.EXPAND | wx.LEFT)
        self.minInt = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS | wx.TE_PROCESS_ENTER, min=0, max=100, initial=0)
        self.minInt.SetDigits(3)
        self.panel_sizer.Add(self.minInt, pos=(1, 2), span=(1, 1), flag=wx.EXPAND)
        self.maxInt = wx.SpinCtrlDouble(parent=self.panel, style=wx.SP_ARROW_KEYS | wx.TE_PROCESS_ENTER, min=0, max=100, initial=100)
        self.maxInt.SetDigits(3)
        self.panel_sizer.Add(self.maxInt, pos=(2, 2), span=(1, 1), flag=wx.EXPAND)
        self.panel_sizer.Add(wx.StaticText(self.panel, label='%'), pos=(1, 3), span=(1, 1))
        self.panel_sizer.Add(wx.StaticText(self.panel, label='%'), pos=(2, 3), span=(1, 1))

        # Add Figure
        self.figure = Figure()
        self.axes = self.figure.add_subplot(111)
        self.canvas = FigureCanvas(self, -1, self.figure)
        # self.panel_sizer.Add(self.canvas, 1, wx.LEFT | wx.TOP | wx.EXPAND)
        self.panel_sizer.Add(self.canvas, pos=(3, 0), span=(1, 4), flag=wx.EXPAND)

        # Add toolbar
        self.toolbar = NavigationToolbar2Wx(self.canvas)
        self.toolbar.Realize()
        # By adding toolbar in sizer, we are able to put it at the bottom
        # of the frame - so appearance is closer to GTK version.
        # self.panel_sizer.Add(self.toolbar, 0, wx.LEFT | wx.EXPAND)
        self.panel_sizer.Add(self.toolbar, pos=(4, 0), span=(1, 4), flag=wx.EXPAND)
        # update the axes menu on the toolbar
        self.toolbar.update()

        self.panel.SetSizer(self.panel_sizer)
        self.main_sizer.Add(self.panel, 1, wx.GROW)

        self.SetSizer(self.main_sizer)
        self.SetAutoLayout(True)
        self.main_sizer.Fit(self)

    def setConnections(self):
        """
        Set Event Handlers
        """
        self.canvas.mpl_connect('button_press_event', self.onClicked)
        self.Bind(wx.EVT_BUTTON, self.flipPlotX, self.flip_x)
        self.Bind(wx.EVT_BUTTON, self.flipPlotY, self.flip_y)
        self.Bind(wx.EVT_BUTTON, self.update_plot, self.full_button)
        self.Bind(wx.EVT_COMBOBOX, self.update_plot, self.colors)
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
        self.x, self.y, self.z = self.plotter.getXYZ()

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

            self.axes.cla()
            self.axes.pcolormesh(self.x, self.y, z, cmap=str(self.colors.GetValue()))

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
            self.axes.set_xlabel(coord_info)
            self.canvas.draw()
            print(coord_info)