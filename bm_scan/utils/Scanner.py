from os.path import exists, join
# import Mp
# import MpScan

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
        """
        Generate scan record file with scan record descriptions
        """
        # Pick a file name
        file_name = "scan_record.dat"
        if exists(join(self.dir_path, file_name)):
            i = 1
            while exists(join(self.dir_path, 'scan_record_'+str(i)+'.dat')):
                i += 1
            self.scanrecord_file = 'scan_record_'+str(i)+'.dat'
        else:
            self.scanrecord_file = file_name

        # file = open(self.scanrecord_file, 'w')
        # des = ['xmotor','scan','linear_scan', 'motor_scan', '\"\" \"\"', '1', '1', '1', self.x_motor, '1', self.scalars[0],
        #        '0', '0', '\"'+str(self.dwell_time)+' joerger_timer\"', 'text', self.x_motor+"_result.001", 'none', '$f[0]', self.x_start, self.x_size, self.x_step]
        # file.write("\t".join(des))
        # file.close()
        # print join(self.dir_path, self.scanrecord_file), 'is generated.'

    def performScan(self):
        """
        Performing scan by using scan record which produced from generateScanRecord()
        and return scan data file name
        """
        return ""
        record_list = Mp.setup_database("/etc/mx/motor.dat") # Get record list from DB
        scan_database = join(self.dir_path, self.scanrecord_file)
        MpScan.load_scans(record_list, scan_database)
        scan_name = "xmotor"

        scan = record_list.get_record(scan_name)

        # Perform scan "vslit"
        print "performing vslit scan."
        scan.perform_scan()

        return self.x_motor+"_result.001"
