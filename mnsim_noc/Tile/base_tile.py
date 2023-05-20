# -*-coding:utf-8-*-
"""
@FileName:
    base_tile.py
@Description:
    Base Tile class for time slice
@CreateTime:
    2021/10/08 17:57
"""
import copy
import math
from mnsim_noc.utils.component import Component
from mnsim_noc.Buffer import MultiInputBuffer, MultiOutputBuffer

class BaseTile(Component):
    """
    Base Tile class for behavior-driven simulation
    position: tuple -> (row, column)
    tile_behavior_cfg: dict (key, value):
        task_id, layer_id, tile_id, target_tile_id, and dependence
        dependence is a list, each item is a dict
            wait, output, drop, and latency
    """
    REGISTRY = "tile"
    NAME = "behavior_driven"
    def __init__(self, position, image_num, buffer_size, tile_behavior_cfg, sample_list):
        """
        image_num: int, throughput
        buffer_size: tuple of int, (buffer_size_input, buffer_size_output), bits
        """
        super(BaseTile, self).__init__()
        # position and tile_behavior_cfg
        self.position = position
        self.image_num = image_num
        self.tile_behavior_cfg = copy.deepcopy(tile_behavior_cfg)
        # sample list
        self.sample_list = sample_list
        # other parameters
        self.task_id = tile_behavior_cfg["task_id"] # value
        self.tile_id = tile_behavior_cfg["tile_id"] # value
        self.layer_id = tile_behavior_cfg["layer_id"] # value
        self.target_tile_id = tile_behavior_cfg["target_tile_id"] # this is a list
        self.source_tile_id = tile_behavior_cfg["source_tile_id"] # this is a list
        self.control_tile_id = tile_behavior_cfg["control_tile_id"] # none if not controlled by other tiles
        self.exit_id = tile_behavior_cfg["exit_id"] # none if not the agg tile of last fc of exit
        # exit table, for controled tile
        if self.control_tile_id is not None:
            self.exit_table = {'id':-1, 'table':[]}
            self.source_tile_id.append(self.control_tile_id)
        else: self.exit_table = None
        # dependence length, for adapt to different image num
        self.dependence_length = len(self.tile_behavior_cfg["dependence"])
        self.is_commit = self.dependence_length == 0
        # input buffer and output buffer
        self.input_buffer = MultiInputBuffer(buffer_size[0], self.source_tile_id, self.exit_table)
        self.output_buffer = MultiOutputBuffer(buffer_size[1], self.target_tile_id, self.exit_table, self.control_tile_id)
        # running state, False for idle, True for running
        self.running_state = False
        self.computation_list = self._get_computation_list()
        self.computation_id = 0
        self.computation_end_time = float("inf")
        self.computation_range_time = []
        
    def _get_computation_list(self):
        """
        get the computation list
        each item is a tuple, (dependence, done_flag)
        done_flag, idle, running, done
        """
        computation_list = []
        # if is the exit tile
        if self.exit_id is not None:
            data_len = math.ceil(math.log2(self.image_num))+1+10
            for i in range(self.image_num):
                exit_choice = self.sample_list[i][self.exit_id]
                for j in range(self.dependence_length):
                    dependence = copy.deepcopy(self.tile_behavior_cfg["dependence"][j])
                    # modify dependence base on image num
                    for key in ["wait", "drop"]:
                        for value in dependence[key]:
                            # x, y, start, end, bit, total, image_id, layer_id, tile_id
                            value[6] = i
                    # x, y, start, end, bit, total, image_id, layer_id, tile_id
                    dependence["output"] = [[-1, -1, -1, -1, -1, -1, i, exit_choice, data_len, self.tile_id]]
                    computation_list.append([dependence, "idle"])
        else:
            for i in range(self.image_num):
                for j in range(self.dependence_length):
                    dependence = copy.deepcopy(self.tile_behavior_cfg["dependence"][j])
                    # modify dependence base on image num
                    for key in ["wait", "output", "drop"]:
                        for value in dependence[key]:
                            # x, y, start, end, bit, total, image_id, layer_id, tile_id
                            value[6] = i
                    computation_list.append([dependence, "idle"])
        # if not self.is_commit:
        #     print("tile: {}, computation len: {}, latency: {}, final_lat: {}".format(self.tile_id, int(len(computation_list)/self.image_num), int(computation_list[0][0]["latency"]),int(len(computation_list)/self.image_num)*int(computation_list[0][0]["latency"])))
        return computation_list

    def update(self, current_time):
        """
        suppose the time reaches current_time
        for different running state, update the tile
        ONLY update function can change the running_state
        """
        # first for the running state, can change to idle
        if self.running_state:
            if current_time >= self.computation_end_time:
                # PHASE: Tile COMPUTATION DONE
                # get computation
                computation = self.computation_list[self.computation_id][0]
                # modify state
                self.running_state = False
                self.computation_list[self.computation_id][1] = "done"
                self.computation_id += 1
                # if the img is to be exit
                if self.exit_table and computation["output"][0][6] in self.exit_table['table']:
                    assert self.exit_table['id'] == computation["output"][0][6], "latest img_num:{}, dropped data img_num:{}".format(self.exit_table['id'], computation["output"][0][6])
                # else modify buffer
                else: 
                    self.input_buffer.delete_data_list(computation["drop"])
                    self.output_buffer.add_data_list(computation["output"])
            else:
                return None
        assert self.running_state == False, "running_state should be idle"
        if self.computation_id >= len(self.computation_list):
            # if all computation are done, return None
            self.computation_end_time = float("inf")
            return None
        computation = self.computation_list[self.computation_id][0]
        # TODO: if can skip to next computation, for normal tiles
        tmp_image_id = computation["wait"][0][6]
        possible_image_id = self.input_buffer.get_possible_img_id()
        if possible_image_id and possible_image_id > tmp_image_id:
            # print("tile {}, skip computation, possible_image_id:{}, tmp_image_id:{}".format(self.tile_id, possible_image_id, tmp_image_id))
            self.computation_list = list(filter(lambda x: x[1]=='done' or x[0]["wait"][0][6] >= possible_image_id, self.computation_list))
            assert self.computation_list[self.computation_id][0]["wait"][0][6] >= possible_image_id, "wrong skip computation list 1"
            assert self.computation_list[self.computation_id][1] == "idle", "tile {}, wrong skip computation list 2, {}".format(self.tile_id,self.computation_list)
            if self.computation_id > 0:
                assert self.computation_list[self.computation_id-1][1] == "done", "wrong skip computation list 3"
            assert self.computation_id < len(self.computation_list), "wrong skip computation list 4"
        # refresh the computation
        computation = self.computation_list[self.computation_id][0]
        # for idle state, running state is False
        # check if the computation can run
        # PHASE: TILE COMPUTATION JUDGE
        if self.input_buffer.check_data_already(computation["wait"]) \
            and self.output_buffer.check_enough_space(computation["output"]):
            # PHASE: TILE COMPUTATION START
            self.running_state = True
            self.computation_list[self.computation_id][1] = "running"
            assert computation["latency"] > 0, "latency should be positive"
            self.computation_end_time = current_time + computation["latency"]
            self.computation_range_time.append((current_time, self.computation_end_time))
            return None
        self.computation_end_time = float("inf")
        return None

    def get_computation_end_time(self):
        """
        get the end time of the computation
        """
        if self.running_state:
            return self.computation_end_time
        return float("inf")

    def get_computation_range(self):
        """
        get the range of the computation
        """
        computation_range = []
        dependence_length = len(self.tile_behavior_cfg["dependence"])
        for i in range(self.image_num):
            computation_range.append([])
            for j in range(dependence_length):
                computation_range[-1].append(self.computation_range_time[i*dependence_length+j])
        return computation_range

    def check_finish(self):
        """
        check if the tile is finished
        """
        assert self.running_state == False, f"{self.tile_id} running_state should be idle"
        # if the tile is the first tile, then it should perform all the computation
        # for other tiles, if the computation is stucked, then there must remain data in output buffer
        if len(self.source_tile_id) == 1 and self.source_tile_id[0] == -1:
            assert self.computation_id == len(self.computation_list), \
                f"{self.tile_id} computation_id should to the end of the list"
        assert self.computation_end_time == float("inf"), \
            f"{self.tile_id} computation_end_time should be inf"
        self.input_buffer.check_finish()
        self.output_buffer.check_finish(self.image_num)

    def get_running_rate(self, end_time):
        """
        get the simulation result
        """
        self.check_finish()
        # get the range of the computation
        computation_time = sum([
            end - start for start, end in self.computation_range_time
        ])
        return computation_time * 1. / end_time
    
    def update_exit_table(self, exit_data):
        """
        control data: (-1, -1, -1, -1, -1, -1, image_id, exit, length, -1)
        """
        for data in exit_data:
            assert data[6] > self.exit_table['id'], "img_id should be larger than lastest img_num"
            if data[7]:
                self.exit_table['table'].append(data[6])
            self.exit_table['id'] = data[6]
            # fflush buffer
            self.input_buffer.filter_exit_table()
            self.output_buffer.filter_exit_table()
            # update computation list
            self.filtered = list(filter(lambda x: not(x[1]!='idle' or x[0]["output"][0][6] not in self.exit_table['table']), self.computation_list))
            self.computation_list = list(filter(lambda x: x[1]!='idle' or x[0]["output"][0][6] not in self.exit_table['table'], self.computation_list))
            # print("tile {}, update exit table, {}, filtered, {}, {}".format(self.tile_id, self.exit_table, self.filtered, self.computation_list))
        
        # tmp_keep_list = []
        # for index, computation in enumerate(self.computation_list):
        #     # delete the computation which shouldn't be executed
        #     if computation[1] == "idle" and computation[0]["output"][0][6] in self.exit_table['table']:
        #         assert index > self.computation_id, "drop computation that is or has been executed"
        #     else: # keep the computation
        #         tmp_keep_list.append(index)
        # self.computation_list = [self.computation_list[i] for i in tmp_keep_list]
