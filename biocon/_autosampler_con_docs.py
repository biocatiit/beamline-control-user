"""
Formulating a set of commands for the automator:
- Each command goes to a single control queue (e.g. hplc, coflow, exposure)

- Commands are run independently of each other

- Synchronization is done through "wait" commands.

- Wait commands can either be time based (e.g. wait 60 s) or status based,
which wait until one or more instruments are in a specified state

- Status based wait commands must start with wait, but can otherwise have
an arbitrary name. So you can be descriptive, e.g. "wait_finish" or "wait_sample"

An example of a wait command would be:
sample_wait_id = self.automator.get_wait_id()
sample_wait_cmd = 'wait_sample_{}'.format(sample_wait_id)
cmd_id1 = self.automator.add_cmd('exp', sample_wait_cmd, [], {'condition': 'status',
    'inst_conds': [[hplc_inst, [sample_wait_cmd,]], ['exp', [sample_wait_cmd,]]]})

Here the get_wait_id() function is used to get a unique ID for the wait command
This allows you to have the queue wait until the relevant instrument is waiting
for the particular command. In this case, we tell the exposure queue to wait
until both the hplc and the exposure queue are waiting for this particular
sample_wait command, this ensures that the proper sample injection and
exposure are started synchronously.

Note that wait commands need to be added on all relevant instruments.


It is recommended best practice to always put a wait_finish that waits for
instrument idle at the end of a set of commands, so the next set doesn't start,
which can interfere with the ability to move commands around/change command
parameters.


An example set of commands that starts two instruments and synchronize between them
would be this exposure command:
"""
hplc_inst = item_info['inst']

sample_wait_id = self.automator.get_wait_id()
sample_wait_cmd = 'wait_sample_{}'.format(sample_wait_id)
finish_wait_id = self.automator.get_wait_id()
finish_wait_cmd = 'wait_finish_{}'.format(finish_wait_id)


# This tells the exposure to wait until both the HPLC and the exposure queues
# reach the same place before starting the exposure.
cmd_id1 = self.automator.add_cmd('exp', sample_wait_cmd, [], {'condition': 'status',
    'inst_conds': [[hplc_inst, [sample_wait_cmd,]], ['exp', [sample_wait_cmd,]]]})

# This tells the exposure queue that once both instrumetns reach the wait, start
# the actual exposure (note this is still hardware triggered per the exposure settings)
cmd_id2 = self.automator.add_cmd('exp', 'expose', [], item_info)

# This tells the exposure queue to wait until both exposure and hplc run are done
# before moving on to the next command (means later commands stay queued and don't enter
# a wait/running state while either instrument is running)
cmd_id3 = self.automator.add_cmd('exp', finish_wait_cmd, [], {'condition': 'status',
    'inst_conds': [[hplc_inst, ['idle',]], ['exp', ['idle',]]]})

# Sets up injection method parameters
inj_settings = {
    'sample_name'   : item_info['sample_name'],
    'acq_method'    : item_info['acq_method'],
    'sample_loc'    : item_info['sample_loc'],
    'inj_vol'       : item_info['inj_vol'],
    'flow_rate'     : item_info['flow_rate'],
    'elution_vol'   : item_info['elution_vol'],
    'flow_accel'    : item_info['flow_accel'],
    'pressure_lim'  : item_info['pressure_lim'],
    'result_path'   : item_info['result_path'],
    'sp_method'     : item_info['sp_method'],
    'wait_for_flow_ramp'    : item_info['wait_for_flow_ramp'],
    'settle_time'   : item_info['settle_time'],
    }

# Tells the HPLC to wait until both the HPLC and exposure queues reach the
sample place befoer starting the injection
cmd_id4 = self.automator.add_cmd(hplc_inst, sample_wait_cmd, [],
    {'condition': 'status', 'inst_conds': [[hplc_inst,
    [sample_wait_cmd,]], ['exp', [sample_wait_cmd,]]]})

# Tells the HPLC to wait until the exposure has started to start the
# injection. Note that the exposure is stll hardware triggered per the
# exposure settings, this just make sure the exposure is ready to go before
# injection takes place
cmd_id5 = self.automator.add_cmd(hplc_inst, sample_wait_cmd, [],
                {'condition': 'status', 'inst_conds': [[hplc_inst,
                [sample_wait_cmd,]], ['exp', ['idle',]]]})

# Tells the HPLC to inject
cmd_id6 = self.automator.add_cmd(hplc_inst, 'inject', [], inj_settings)

# Tells the HPLC queue to wait until both exposure and hplc run are done
# before moving on to the next command
cmd_id7 = self.automator.add_cmd(hplc_inst, finish_wait_cmd, [],
    {'condition': 'status', 'inst_conds': [[hplc_inst,
    ['idle',]], ['exp', ['idle',]]]})

#accounts for delayed update time between run queue and instrument status
cmd_id7 = self.automator.add_cmd(hplc_inst, 'wait_time', [],
    {'condition': 'time', 't_wait': 1})
