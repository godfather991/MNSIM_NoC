# -*-coding:utf-8-*-
"""
@FileName:
    conv_time_slice_tile.py
@Description:
    CONV Tile class for time slice
@CreateTime:
    2021/11/1 21:00
"""
from mnsim_noc.Tile import TimeSliceTile


class CONVTimeSliceTile(TimeSliceTile):
    NAME = "conv_time_slice_tile"

    def __init__(self, position, task_cfg):
        # input and output data
        # format: (start_tile_id, end_tile_id, layer, x, y, length)
        """
        task_cfg properties:
            length:
                length of output data
            layer_in:
                Input layer
            layer_out:
                Output layer
            num_in:
                Number of inputs required for a node in input feature map
            height_core; width_core; stride_core; padding_core:
                Parameter of the convolution kernel
            height_input; width_input:
                height and width of the input feature
            height_output; width_output:
                height and width of the output feature
            computing_time:
                Number of time slice required for computing a node on output feature
            end_tiles:
                List of id of tiles where the outputs should be sent to
        """
        super().__init__(self, position, task_cfg)
        # Extract parameters from task_cfg
        self.height_core = task_cfg.height_core
        self.width_core = task_cfg.width_core
        self.stride_core = task_cfg.stride_core
        self.padding_core = task_cfg.padding_core
        # Coordinate of the output under computation on the output feature map
        self.computing_output = None
        # Coordinate of the output to be computed next on the output feature map
        self.next_output = (1, 1)
        # Coordinate of the bottom right corner of the useless input
        # format: (x, y, h)
        self.useless = (0, 0, 0)

    def update_time_slice(self):
        # Computing process in conv tile
        # if the tile was not computing
        if self.state == 0:
            # allocate computation task
            if self.input_list:
                x_req = min(self.height_input,
                            self.height_core + self.stride_core * (self.next_output[0] - 1) - self.padding_core)
                y_req = min(self.width_input,
                            self.width_core + self.stride_core * (self.next_output[1] - 1) - self.padding_core)
                # if the input_list satisfy the requirement for next output, then allocate the computation task
                if (self.latest_input[0] * self.width_input + self.latest_input[1]) >= (
                        x_req * self.width_input + y_req):
                    # update self.useless
                    if x_req == self.height_input:
                        x_useless = x_req
                        h_useless = self.height_core
                    else:
                        x_useless = min(x_req - self.height_core + self.stride_core, self.height_core)
                        h_useless = self.stride_core
                    if y_req == self.width_input:
                        y_useless = y_req
                    else:
                        y_useless = min(y_req - self.width_core + self.stride_core, self.width_input)
                    self.useless = (x_useless, y_useless, h_useless)
                    # update self.computing_output
                    self.computing_output = self.next_output
                    # update self.next_output
                    x_new = (self.next_output[0] * self.width_output + self.next_output[1]) // self.width_output
                    y_new = (self.next_output[0] * self.width_output + self.next_output[1]) % self.width_output + 1
                    self.next_output = (x_new, y_new)
                    # update state
                    self.state = self.computing_time
        # compute in the time slice
        if self.state > 0:
            self.state -= 1
        # if the tile just finished the computation
        if self.state == 0:
            if self.computing_output:
                self.output_list.append(self.computing_output)
                self.computing_output = None
                # delete useless inputs from input_list considering the self.useless
                list_for_search = self.input_list
                for single_input in list_for_search:
                    if single_input[0] <= self.useless[0] - self.useless[2] or (
                            single_input[0] <= self.useless[0] and single_input[1] <= self.useless[1]):
                        self.input_list.remove(single_input)