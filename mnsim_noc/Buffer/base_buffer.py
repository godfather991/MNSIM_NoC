#-*-coding:utf-8-*-
"""
@FileName:
    base_buffer.py
@Description:
    Base Buffer class for behavior-driven simulation
@Authors:
    Hanbo Sun(sun-hb17@mails.tsinghua.edu.cn)
@CreateTime:
    2022/05/07 09:27
"""
import copy
from mnsim_noc.utils.component import Component

def get_data_size(data):
    """
    get the size of the data
    data: (x, y, start, end, bit, total, image_id, layer_id, in_id, tile_id)
    control: (-1, -1, -1, -1, -1, -1, image_id, exit, length, tile_id)
    """
    assert len(data) == 10, "data length should be 10"
    if data[0] >= 0:
        return (data[3] - data[2]) * data[4]
    else: return data[8]

def get_data_tile(data):
    """
    check if the data is from the tile
    """
    return data[9]

class BaseBuffer(Component):
    """
    Base Buffer class for behavior-driven simulation
    """
    REGISTRY = "buffer"
    NAME = "behavior_driven"
    def __init__(self, buffer_size, exit_table):
        """
        buffer_size: buffer size in bits
        """
        super(BaseBuffer, self).__init__()
        self.exit_table = exit_table
        self.buffer_size = buffer_size
        self.buffer_data = []
        self.used_space = 0

    def _add_one(self, data):
        """
        add one data to the buffer
        """
        # perhaps add a check to make sure data is not in the buffer
        self.buffer_data.append(data)
        self.used_space += get_data_size(data)

    def add_data_list(self, data_list):
        """
        add list data to the buffer
        """
        for data in data_list:
            self._add_one(data)

    def _delete_one(self, data):
        """
        delete one data in the buffer
        """
        # perhaps add a check to make sure data is in the buffer
        self.buffer_data.remove(data)
        self.used_space -= get_data_size(data)

    def delete_data_list(self, data_list):
        """
        delete list data in the buffer
        """
        for data in data_list:
            self._delete_one(data)

    def filter_exit_table(self):
        """
        filter the data in the buffer by the exit table
        """
        assert self.exit_table is not None, "exit table is None"
        tmp_drop_list = list(filter(lambda x: x[6] in self.exit_table['table'], self.buffer_data))
        # for data in self.buffer_data:
        #     if data[6] in self.exit_table['table']:
        #         tmp_drop_list.append(data)
        buffer_data = copy.deepcopy(self.buffer_data)
        self.delete_data_list(tmp_drop_list)
        return(buffer_data[-1] if len(buffer_data) > 0 else buffer_data)