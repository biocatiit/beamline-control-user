from os.path import exists

import pandas as pd
import numpy as np

from formula import calculate

class Plotter(object):
    """
    A class to process scan data from a :mod:`Scanner` scan, and send it to a plot
    """
    def __init__(self, motor_x, motor_y, formula, x_step=None, y_step=None, columns=None):
        """
        Initializes the plotter

        :param str motor_x: The x motor name.
        :param str motor_y: The y motor name.
        :param str formula: The plot formula.
        :param float x_step: The step size in x.
        :param float y_step: The step size in y.
        :param columns: The column names. Defaults to None.
        """
        self.motor_x = motor_x
        self.motor_y = motor_y
        self.formula = formula
        self.columns = columns
        self.scandata = None
        self.x_step = x_step
        self.y_step = y_step

    def read(self, full_path):
        """
        Read scan data file and encapsulate data

        :param str full_path: The path to the scan data file.

        :returns: True if the file exists, False otherwise.
        :rtype: bool

        """
        if not exists(full_path):
            print(str(full_path)+ " does not exist")
            return False

        # Load data
        data = np.loadtxt(full_path)

        # Number of column
        n_cols = data.shape[1]

        # Get Column names
        self.columns = get_cols(full_path)

        # Remove detectors
        self.columns = self.columns[:n_cols]

        # Put/Add data to pandas dataframe
        scandata = pd.DataFrame(data, columns=self.columns)
        if self.scandata is None:
            self.scandata = scandata
        else:
            self.scandata = pd.concat([self.scandata, scandata])

        # sort data by motory and motorx position
        self.scandata = self.scandata.sort_values(by=[self.motor_y, self.motor_x]) # TODO

        # Remove duplicate data
        self.scandata = self.scandata.drop_duplicates(keep='first')
        return True

    def getXYZ(self):
        """
        Create map from scan data.

        :returns: Three items, either the x and y coordinates as the result
            of a np.meshgrid call and the intensity value calculated by
            the ``formula``, in a grid. If the scan data is not there, it returns
            None three times.
        :rtype: np.array, np.array, np.array
        """

        if self.scandata is not None:
            scan_data = self.scandata.copy()
            x = sorted(list(set(np.array(scan_data[self.motor_x]))))
            y = sorted(list(set(np.array(scan_data[self.motor_y]))))

            # Set x and y step if they're available
            if self.x_step is not None:
                xs = self.x_step
            else:
                if len(x) > 1:
                    xs = x[1] - x[0]
                else:
                    xs = 1

            if self.y_step is not None:
                ys = self.y_step
            else:
                if len(y) > 1:
                    ys = y[1] - y[0]
                else:
                    ys = xs

            # Cauculate new data from formula
            d = {}
            for c in self.columns:
                d[c] = np.array(scan_data[c])

            z = calculate(self.formula, d)

            # Reshape for displaying
            z = np.reshape(z, (len(y), len(x)))

            z[np.isinf(z)] = 0.

            # Add 1 row and 1 column to support pcolormesh (pcolormesh requires 1 extra row and column to display)
            x.append(max(x) + xs)
            y.append(max(y) + ys)

            x_coor, y_coor = np.meshgrid(x, y)

            return x_coor, y_coor, z
        else:
            return None, None, None

def get_cols(full_path):
    """
    Get all column names from a scan text file

    :param str full_path: full directory of text file

    :returns: all column names
    :rtype: list
    """
    file = open(full_path, 'r')
    # Get column names
    cols = []
    for line in file:
        # Go to each line and file '%device'
        if "%devices" in line:
            toks = line.split()
            equal_ind = toks.index('=')
            for i in range(equal_ind + 1, len(toks)):
                c = toks[i]
                if len(c) > 0:
                    c = c.rstrip('\n')
                    c = c.rstrip(';')
                    cols.append(c)
            break
    return cols
