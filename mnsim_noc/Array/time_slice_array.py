# -*-coding:utf-8-*-
"""
@FileName:
    time_slice_tile.py
@Description:
    Array class for time slice
@Authors:
    Hanbo Sun(sun-hb17@mails.tsinghua.edu.cn)
@CreateTime:
    2021/10/08 18:28
"""
from copy import copy
import re
import os
import copy
import math
import configparser as cp
import matplotlib.pyplot as plt
import numpy as np
from mnsim_noc.Array import BaseArray
from mnsim_noc.Tile import FCTimeSliceTile, CONVTimeSliceTile, PoolingTimeSliceTile
from mnsim_noc.Wire import TimeSliceWire, NoConflictsWire
from mnsim_noc.Router import TimeSliceRouter, NoConflictsRouter
from mnsim_noc.Data.data import Data
from MNSIM.Latency_Model.Model_latency import tile_latency_analysis,pooling_latency_analysis


class TimeSliceArray(BaseArray):
    NAME = "time_slice_array"
    '''
    array_cfg: 
    time_slice: span of a time_slice (ns)
    sim_config_path: hardware description
    '''
    def __init__(self, tcg_mapping, time_slice, sim_config_path, inter_tile_bandwidth, input_cache_size, output_cache_size, packet_size, no_communication_conflicts, allow_pipeline, quiet):
        super().__init__(tcg_mapping)
        # span of timeslice: ns
        self.time_slice = time_slice
        self.sim_config_path = sim_config_path
        tcg_config = cp.ConfigParser()
        tcg_config.read(sim_config_path, encoding='UTF-8')
        # bandwidth of the wire: Gbps
        self.bandwidth = float(tcg_config.get('Tile level', 'Inter_Tile_Bandwidth'))
        # self.bandwidth  = int(inter_tile_bandwidth)
        self.clock_num = 0
        self.tile_dict = dict()
        self.wire_dict = dict()
        self.wire_data_transferred = dict()
        # tile_id/wire_id -> block_num
        self.block_dict = dict()
        # detailed location of each block
        # e.g. [block 0: (0,0,7,7), block 1: (8,0,15,7)]
        self.block_allocate = []
        self.layer_cfg = []
        self.next_slice_num = 1
        self.roofline = 0
        self.roofline_constrain = []
        self.roofline_record = []
        self.total_computing_power = 0
        self.total_data_size = 0
        self.input_cache_size = input_cache_size
        self.output_cache_size = output_cache_size
        self.packet_delay = math.ceil(float(packet_size) * 8 / self.bandwidth / self.time_slice)
        self.no_communication_conflicts = no_communication_conflicts
        # pipeline
        self.allow_pipeline = allow_pipeline
        self.pipeline_num = 50 if(allow_pipeline) else 1
        self.quiet = quiet

    def task_assignment(self):
        # save the data length from previous layer
        # Convert the layer_info
        for layer_id in range(self.tcg_mapping.layer_num):
            layer_dict = self.tcg_mapping.net[layer_id][0][0]
            cfg = dict()
            # TODO: extended to support branch
            if len(self.tcg_mapping.layer_tileinfo[layer_id]['Inputindex']) > 1:
                self.logger.warning('Do not support branch')
            cfg['layer_in'] = self.tcg_mapping.layer_tileinfo[layer_id]['Inputindex'][0] + layer_id
            if len(self.tcg_mapping.layer_tileinfo[layer_id]['Outputindex']) > 1:
                self.logger.warning('Do not support branch')
            elif len(self.tcg_mapping.layer_tileinfo[layer_id]['Outputindex']) == 0:
                cfg['tile_out'] = -1
            else:
                cfg['tile_out'] = self.tcg_mapping.layer_tileinfo[layer_id]['Outputindex'][0] + layer_id
            cfg['layer_out'] = layer_id
            cfg['tile_num'] = self.tcg_mapping.layer_tileinfo[layer_id]['tilenum']
            cfg['tile_id'] = []
            cfg['aggregate_arg'] = self.tcg_mapping.aggregate_arg[layer_id]
            if layer_dict['type'] == 'conv':
                cfg['type'] = 'conv'
                cfg['height_input'] = int(layer_dict['Inputsize'][0])
                cfg['width_input'] = int(layer_dict['Inputsize'][1])
                cfg['height_output'] = int(layer_dict['Outputsize'][0])
                cfg['width_output'] = int(layer_dict['Outputsize'][1])
                cfg['height_core'] = int(layer_dict['Kernelsize'])
                cfg['width_core'] = int(layer_dict['Kernelsize'])
                cfg['stride_core'] = int(layer_dict['Stride'])
                cfg['padding_core'] = int(layer_dict['Padding'])
                temp_tile_latency = tile_latency_analysis(SimConfig_path=self.sim_config_path,
                                                read_row=self.tcg_mapping.layer_tileinfo[layer_id]['max_row'],
                                                read_column=self.tcg_mapping.layer_tileinfo[layer_id]['max_column'],
                                                indata=0, rdata=0, inprecision=int(layer_dict['Inputbit']),
                                                PE_num=self.tcg_mapping.layer_tileinfo[layer_id]['max_PE'],
                                                default_inbuf_size=self.tcg_mapping.max_inbuf_size,
                                                default_outbuf_size=self.tcg_mapping.max_outbuf_size
                                                )
                cfg['computing_time'] = math.ceil(temp_tile_latency.tile_latency/self.time_slice)
                cfg['length'] = int(layer_dict['Outputchannel']) * int(
                    layer_dict['outputbit']) / self.bandwidth / self.time_slice
                cfg['data_size'] = math.ceil(int(layer_dict['Outputchannel']) * int(layer_dict['outputbit'])/cfg['tile_num'])
                input_tmp = cfg['height_core']*cfg['width_input']
            elif layer_dict['type'] == 'pooling':
                cfg['type'] = 'pooling'
                cfg['height_input'] = int(layer_dict['Inputsize'][0])
                cfg['width_input'] = int(layer_dict['Inputsize'][1])
                cfg['height_output'] = int(layer_dict['Outputsize'][0])
                cfg['width_output'] = int(layer_dict['Outputsize'][1])
                cfg['height_filter'] = int(layer_dict['Kernelsize'])
                cfg['width_filter'] = int(layer_dict['Kernelsize'])
                cfg['stride_filter'] = int(layer_dict['Stride'])
                cfg['padding_filter'] = int(layer_dict['Padding'])
                temp_pooling_latency = pooling_latency_analysis(SimConfig_path=self.sim_config_path,
                                                        indata=0, rdata=0, outprecision = int(layer_dict['outputbit']),
                                                        default_inbuf_size = self.tcg_mapping.max_inbuf_size,
                                                        default_outbuf_size = self.tcg_mapping.max_outbuf_size,
                                                        default_inchannel = int(layer_dict['Inputchannel']), default_size = (int(layer_dict['Kernelsize'])**2))
                cfg['computing_time'] = math.ceil(temp_pooling_latency.pooling_latency/self.time_slice)
                cfg['length'] = int(layer_dict['Outputchannel']) * int(
                    layer_dict['outputbit']) / self.bandwidth / self.time_slice
                cfg['data_size'] = math.ceil(int(layer_dict['Outputchannel']) * int(layer_dict['outputbit'])/cfg['tile_num'])
                input_tmp = cfg['height_filter']*cfg['width_input']
            elif layer_dict['type'] == 'fc':
                cfg['type'] = 'fc'
                cfg['height_input'] = int(layer_dict['Infeature']) / int(self.tcg_mapping.net[layer_id-1][0][0]['Outputchannel'])
                cfg['width_input'] = 0
                cfg['height_output'] = int(layer_dict['Outfeature'])
                cfg['width_output'] = 0
                temp_tile_latency = tile_latency_analysis(SimConfig_path=self.sim_config_path,
                                read_row=self.tcg_mapping.layer_tileinfo[layer_id]['max_row'],
                                read_column=self.tcg_mapping.layer_tileinfo[layer_id]['max_column'],
                                indata=0, rdata=0, inprecision=int(layer_dict['Inputbit']),
                                PE_num=self.tcg_mapping.layer_tileinfo[layer_id]['max_PE'],
                                default_inbuf_size=self.tcg_mapping.max_inbuf_size,
                                default_outbuf_size=self.tcg_mapping.max_outbuf_size
                                )
                cfg['computing_time'] = math.ceil(temp_tile_latency.tile_latency/self.time_slice)
                cfg['length'] = int(layer_dict['outputbit']) / self.bandwidth / self.time_slice
                cfg['data_size'] = math.ceil(int(layer_dict['outputbit'])/cfg['tile_num'])
                input_tmp = cfg['height_input']
            else:
                self.logger.warning('Unsupported layer type, layer_id:' + str(layer_id))
            cfg['input_cache'] = math.ceil(int(self.input_cache_size) * 1024 * 8 / self.bandwidth / self.time_slice)
            cfg['output_cache'] = math.ceil(int(self.output_cache_size) * 1024 * 8 / self.bandwidth / self.time_slice)
            # ensure the output cache of the last layer
            if layer_id == self.tcg_mapping.layer_num-1:
                cfg['output_cache'] = float('inf')
            if layer_id > 0:
                last_layer_dict = self.tcg_mapping.net[layer_id-1][0][0]
                cfg['input_size'] = math.ceil(int(last_layer_dict['Outputchannel']) * int(
                    last_layer_dict['outputbit']))
                # TODO: consider the fc input_length
            else:
                cfg['input_size'] = 0
            # if the cache can take in one output
            if cfg['data_size'] > cfg['output_cache']:
                self.logger.warn('output cache size too small, should be more than '+str(math.ceil(cfg['data_size']/8))+'B')
                exit()
            if cfg['input_size']*input_tmp > cfg['input_cache']:
                self.logger.warn('output cache size too small, should be more than '+str(math.ceil(cfg['input_size']*input_tmp/8))+'B')
                exit()
            self.layer_cfg.append(cfg)
        # generate tile_ids and aggregate_arg for layers
        for i in range(self.tcg_mapping.tile_num[0]):
            for j in range(self.tcg_mapping.tile_num[1]):
                layer_id = int(self.tcg_mapping.mapping_result[i][j])
                if layer_id >= 0:
                    self.layer_cfg[layer_id]['tile_id'].append("{}_{}".format(i, j))
        # allocate the tiles
        for i in range(self.tcg_mapping.tile_num[0]):
            for j in range(self.tcg_mapping.tile_num[1]):
                layer_id = int(self.tcg_mapping.mapping_result[i][j])
                if layer_id >=0:
                    cfg = copy.deepcopy(self.layer_cfg[layer_id])
                    # TODO: extended to support branch
                    # process the aggregate tile
                    if (cfg['aggregate_arg'][0], cfg['aggregate_arg'][1]) == (i, j):
                        if layer_id == self.tcg_mapping.layer_num-1:
                            cfg['end_tiles'] = []
                        else:
                            cfg['end_tiles'] = self.layer_cfg[cfg['tile_out']]['tile_id']
                        cfg['num_out'] = cfg['tile_num']
                    else:
                        if layer_id == self.tcg_mapping.layer_num-1:
                            cfg['end_tiles'] = []
                        else:
                            cfg['end_tiles'] = ["{}_{}".format(int(cfg['aggregate_arg'][0]), int(cfg['aggregate_arg'][1]))]
                        cfg['num_out'] = 1
                        cfg['length'] = math.ceil(cfg['length'] / cfg['tile_num'])
                    # different tile types
                    if cfg['type'] == 'conv':
                        tile = CONVTimeSliceTile((i, j), cfg, self.time_slice, self.quiet)
                    elif cfg['type'] == 'fc':
                        tile = FCTimeSliceTile((i, j), cfg, self.time_slice, self.quiet)
                    elif cfg['type'] == 'pooling':
                        tile = PoolingTimeSliceTile((i, j), cfg, self.time_slice, self.quiet)
                    # self.logger.info(cfg)
                    self.tile_dict[tile.tile_id] = tile
                    # self.logger.info('layer_id:'+str(layer_id)+' tile_id:'+str(tile.tile_id)+' tile_type:'+cfg['type']+' computing_time:'+str(cfg['computing_time']))
        # allocate the wires
        if self.no_communication_conflicts:
            for i in range(self.tcg_mapping.tile_num[0]):
                for j in range(self.tcg_mapping.tile_num[1]):
                    # North:0; West:1; South:2; East:3;
                    if i > 0:
                        wire = NoConflictsWire((i, j, 0))
                        self.wire_dict[wire.wire_id] = wire
                    if j > 0:
                        wire = NoConflictsWire((i, j, 1))
                        self.wire_dict[wire.wire_id] = wire
                    if i < self.tcg_mapping.tile_num[0] - 1:
                        wire = NoConflictsWire((i, j, 2))
                        self.wire_dict[wire.wire_id] = wire
                    if j < self.tcg_mapping.tile_num[1] - 1:
                        wire = NoConflictsWire((i, j, 3))
                        self.wire_dict[wire.wire_id] = wire      
        else:
            for i in range(self.tcg_mapping.tile_num[0]):
                for j in range(self.tcg_mapping.tile_num[1]):
                    # North:0; West:1; South:2; East:3;
                    if i > 0:
                        wire = TimeSliceWire((i, j, 0))
                        self.wire_dict[wire.wire_id] = wire
                    if j > 0:
                        wire = TimeSliceWire((i, j, 1))
                        self.wire_dict[wire.wire_id] = wire
                    if i < self.tcg_mapping.tile_num[0] - 1:
                        wire = TimeSliceWire((i, j, 2))
                        self.wire_dict[wire.wire_id] = wire
                    if j < self.tcg_mapping.tile_num[1] - 1:
                        wire = TimeSliceWire((i, j, 3))
                        self.wire_dict[wire.wire_id] = wire
        # allocate the router
        if self.no_communication_conflicts:
            self.router = NoConflictsRouter(self.time_slice, self.packet_delay, self.quiet)
        else:
            self.router = TimeSliceRouter(self.time_slice, self.packet_delay, self.quiet)
        # allocate the block
        # TODO: allocate tiles and wires to block using block_allocate
    
    def check_inputs(self, input_image_id):
        for tile_id in self.layer_cfg[0]['tile_id']:
            if not self.tile_dict[tile_id].finish_pipeline(input_image_id-1):
                return False
        return True
    
    def setup_inputs(self, input_image_id):
        # distribute inputs for tiles in layer_0
        inputs_inits = []
        if self.layer_cfg[0]['type'] == 'conv' or self.layer_cfg[0]['type'] == 'pooling':
            for x in range(self.layer_cfg[0]['height_input']):
                for y in range(self.layer_cfg[0]['width_input']):
                    data = Data(x = x + 1,y = y + 1,layer_out=-1, image_id=input_image_id)
                    inputs_inits.append(data)
        elif self.layer_cfg[0]['type'] == 'fc':
            for x in range(self.layer_cfg[0]['height_input']):
                data = Data(x = x + 1,y = -1,layer_out=-1, image_id=input_image_id)
                inputs_inits.append(data)
        for tile_id in self.layer_cfg[0]['tile_id']:
            self.tile_dict[tile_id].update_input(inputs_inits)
            self.tile_dict[tile_id].set_tile_task(self.clock_num)

    def check_finish(self, image_id = 0):
        if self.allow_pipeline:
            # check the output of the last layer
            for tile_id in self.layer_cfg[self.tcg_mapping.layer_num-1]['tile_id']:
                if not self.tile_dict[tile_id].finish_pipeline(image_id):
                    return False
            return True
        else:
            for tile_id, tile in self.tile_dict.items():
                if tile.input_list or (tile.output_list and tile.end_tiles):
                    return False
            for wire_id, wire in self.wire_dict.items():
                if not wire.check_finish():
                    return False
            return True

    def set_wire_task(self, routing_result):
        # task format: (x, y, end_tile_id, length, layer, is_first, is_last)
        # path format: (list[occupied_wire_id], (x, y, end_tile_id, length, layer_out))
        for path in routing_result:
            wire_list = path[0]
            path_data = path[1]
            wire_len = len(wire_list)
            start_tile_id = "{}_{}".format(tuple(map(int, re.findall(r"\d+", wire_list[0])))[0],tuple(map(int, re.findall(r"\d+", wire_list[0])))[1])
            self.tile_dict[start_tile_id].is_transmitting = True
            for index, wire_id in enumerate(wire_list):
                tmp_data = copy.copy(path_data)
                tmp_data.is_first = (index == 0)
                tmp_data.is_last = (index == wire_len - 1)
                self.wire_dict[wire_id].set_wire_task(tmp_data, index * self.packet_delay)

    def update_tile(self):
        for wire_id, wire_datas in self.wire_data_transferred.items():
            for wire_data in wire_datas:
                # wire_data format: list[(x, y, end_tile_id, layer, is_first, is_last)]
                if wire_data.is_first:
                    wire_position = tuple(map(int, re.findall(r"\d+", wire_id)))
                    tile_id = "{}_{}".format(wire_position[0], wire_position[1])
                    self.tile_dict[tile_id].update_output([wire_data])
                if wire_data.is_last:
                    tile_id = wire_data.end_tile_id
                    self.tile_dict[tile_id].update_input([wire_data])
        for tile_id, tile in self.tile_dict.items():
            tile.set_tile_task(self.clock_num)

    def get_timeslice_num(self):
        tmp_timeslice_num = float("inf")
        for tile_id, tile in self.tile_dict.items():
            if tile.computing_output:
                tmp_timeslice_num = min(max(1,tile.state), tmp_timeslice_num)
        for wire_id, wire in self.wire_dict.items():
            tmp_timeslice_num = min(wire.get_timeslice_num(), tmp_timeslice_num)
        return tmp_timeslice_num

    def get_roofline(self):
        for tile_id, tile in self.tile_dict.items():
            tmp_roofline = tile.get_roofline()
            if tmp_roofline:
                if tmp_roofline[0] > self.roofline:
                    self.roofline_constrain = ['Tile_'+tile_id]
                    self.roofline = tmp_roofline[0]
                elif tmp_roofline[0] == self.roofline:
                    self.roofline_constrain.append('Tile_'+tile_id)
                # record the upperbound for painting
                self.roofline_record.append((tmp_roofline[0],'Tile_'+tile_id,'r'))
                # add the computing power and data size
                self.total_computing_power += tmp_roofline[2]/tmp_roofline[3]
                self.total_data_size += tmp_roofline[1]
        for wire_id, wire in self.wire_dict.items():
            tmp_roofline = wire.get_roofline()
            if tmp_roofline:
                if tmp_roofline > self.roofline:
                    self.roofline_constrain = ['Wire_'+wire_id]
                    self.roofline = tmp_roofline
                elif tmp_roofline == self.roofline:
                    self.roofline_constrain.append('Wire_'+wire_id)
                # record the upperbound for painting
                self.roofline_record.append((tmp_roofline,'Wire_'+wire_id,'b'))

    def paint_roofline(self):
        L = len(self.roofline_record)
        X=np.linspace(0,L,20*L)
        Y=np.linspace(self.roofline,self.roofline,20*L)
        plt.plot(X,Y,color='g')
        plt.text(L-50,self.roofline,'Total UpperBound')
        Y=np.linspace(self.total_data_size/self.total_computing_power,self.total_data_size/self.total_computing_power,20*L)
        plt.plot(X,Y,color='g')
        plt.text(L-50,self.total_data_size/self.total_computing_power,'Compute UpperBound')
        for i, item in enumerate(self.roofline_record):
            tmp_X = np.linspace(i,i+0.5,10)
            tmp_Y = np.linspace(item[0],item[0],10)
            plt.plot(tmp_X,tmp_Y,color=item[2])
            plt.yscale('log')
            # plt.text(i+1.2,item[0],item[1])
        plt.savefig('upperbound.png')

    def run(self):
        # task assignment
        self.task_assignment()
        # pipeline
        input_image_id = 0
        computing_image_id = 0
        # start time of each image
        start_time = []
        # compute time of each image
        compute_time = []
        # run for every slice
        while True:
            # check pipeline inputs
            if input_image_id < self.pipeline_num:
                if self.check_inputs(input_image_id):
                    start_time.append(self.clock_num)
                    self.setup_inputs(input_image_id)
                    self.logger.info('(Add input) image '+str(input_image_id)+' Clock num: '+str(self.clock_num))
                    input_image_id += 1
            # check the finish condition
            if self.check_finish(computing_image_id):
                if not self.allow_pipeline:
                    break
                else:
                    compute_time.append(self.clock_num-start_time[computing_image_id])
                    self.logger.info('(Get output) image '+str(computing_image_id)+' Clock num: '+str(self.clock_num))
                    computing_image_id += 1
                    if computing_image_id == self.pipeline_num:
                        break
            # get next_slice_num
            self.next_slice_num = self.get_timeslice_num()
            # self.logger.info('timeslice:'+str(self.next_slice_num))
            # 0, all tile and wire update for one slice
            for tile_id, tile in self.tile_dict.items():
                tile.update_time_slice(self.next_slice_num)
            # get the data transferred by wires
            self.wire_data_transferred = dict()
            for wire_id, wire in self.wire_dict.items():
                self.wire_data_transferred[wire_id] = wire.update_time_slice(self.next_slice_num)
            # 1, record clock_num
            self.clock_num = self.clock_num + self.next_slice_num
            # 2, update tile input and output
            self.update_tile()
            # 3, get all transfer data
            transfer_data = dict()
            tile_state = dict()
            for tile_id, tile in self.tile_dict.items():
                # transfer_data format: (x, y, end_tile_id, length, layer_out)
                transfer_data[tile_id] = tile.get_output()
                tile_state[tile_id] = (tile.input_cache_full(), tile.state, tile.input_image_id, tile.output_image_id, tile.layer_in, tile.layer_out)
            # 4, get all wire state
            wire_state = dict()
            if not self.no_communication_conflicts:
                for wire_id, wire in self.wire_dict.items():
                    wire_state[wire_id] = (wire.next_data==None,)+wire.get_wait_time()
            # 5, routing
            # path format: (list[occupied_wire_id], (x, y, end_tile_id, length, layer_out))
            routing_result = self.router.assign(transfer_data, wire_state, tile_state, self.clock_num)
            # 6, set wire task
            self.set_wire_task(routing_result)
            # os.system('clear')
        # log the simulation time
        if self.allow_pipeline:
            for i in range(0,self.pipeline_num):
                if i > 0:
                    self.logger.info('(Finish) Image '+str(i)+' Pipeline Span: ' + str((start_time[i]-start_time[i-1]) * self.time_slice) + 'ns')
                self.logger.info('(Finish) Image '+str(i)+' Compute Time: ' + str(compute_time[i] * self.time_slice) + 'ns')
            self.logger.info('(Finish) Total Compute Time: ' + str(self.clock_num * self.time_slice) + 'ns')
        else:
            self.logger.info('(Finish) Total Compute Time: ' + str(self.clock_num * self.time_slice) + 'ns')
        # log the roofline
        if not self.no_communication_conflicts:
            self.get_roofline()
            self.paint_roofline()
            self.logger.info('(Upper Bound) Theoretically Shortest Compute Time: ' + str(self.roofline * self.time_slice) + 'ns')
            self.logger.info('(Upper Bound) Theoretically Critical Constrains: ' + str(self.roofline_constrain))
            self.logger.info('(Upper Bound) Actual Utilization Ratio: ' + str(self.roofline / self.clock_num * 100) + '%')