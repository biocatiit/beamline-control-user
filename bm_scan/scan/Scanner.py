class Scanner():
    def __init__(self, dir, x_motor, x_start, x_step, x_size, y_motor, y_start, y_step, y_size, scalars, dwell_time, detector):
        self.dir_path = dir
        self.x_motor = x_motor
        self.x_start = x_start
        self.x_step = x_step
        self.x_size = x_size
        self.y_motor = y_motor
        self.y_start = y_start
        self.y_step = y_step
        self.y_size = y_size
        self.scalars = scalars
        self.dwell_time = dwell_time
        self.detector = detector

    def generateScanRecord(self):
        pass
