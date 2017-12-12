import pandas as pd
import numpy as np
from os.path import exists, join
from formula import calculate
import time

class Plotter:
    """
    A class to create a plot (image) for scan data which performs by Scanner
    """
    def __init__(self, motor_x, motor_y, formula, x_step=None, y_step=None):
        self.motor_x = motor_x
        self.motor_y = motor_y
        self.formula = formula
        self.columns = None
        self.scandata = None
        self.x_step = x_step
        self.y_step = y_step

    def read(self, full_path):
        """
        Read scan data file and encapsulate data
        """
        if not exists(full_path):
            # time.sleep(2)
            print(str(full_path)+ " does not exist")
            return False

        # Load data
        data = np.loadtxt(full_path)

        # Get Column names
        self.columns = get_cols(full_path)

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
        Create map from scan data, display map, and save map as an image to a file
        :return:
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

            # Add 1 row and 1 column to support pcolormesh (pcolormesh requires 1 extra row and column to display)
            x.append(max(x) + xs)
            y.append(max(y) + ys)

            x_coor, y_coor = np.meshgrid(x, y)

            return x_coor, y_coor, z
        else:
            return None, None, None

def get_cols(full_path):
    """
    Get all column names from text file
    :param full_path: full directory of text file
    :return: all column names
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
