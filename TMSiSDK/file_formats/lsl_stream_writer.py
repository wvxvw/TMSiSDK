import sys
from datetime import datetime
import os
import struct
import time

from ..error import TMSiError, TMSiErrorCode
from .. import sample_data_server
from pylsl import StreamInfo, StreamOutlet, local_clock


class LSLConsumer:
    '''
    Provides the .put() method expected by TMSiSDK.sample_data_server

    liblsl will handle the data buffer in a seperate thread. Since liblsl can
    bypass the global interpreter lock and python can't, and lsl uses faster
    compiled code, it's better to offload this than to create our own thread.
    '''

    def __init__(self, lsl_outlet):
        self._outlet = lsl_outlet

    def put(self, sd):
        '''
        Pushes sample data to pylsl outlet, which handles the data buffer

        sd (TMSiSDK.sample_data.SampleData): provided by the sample data server
        '''
        try:
            # split into list of arrays for each sampling event
            signals = [sd.samples[i*sd.num_samples_per_sample_set : \
                                (i+1)*sd.num_samples_per_sample_set] \
                                    for i in range(sd.num_sample_sets)]
            # and push to LSL
            self._outlet.push_chunk(signals, local_clock())
        except:
            raise TMSiError(TMSiErrorCode.file_writer_error)

class LSLWriter:
    '''
    A drop-in replacement for a TSMiSDK filewriter object
    that streams data to labstreaminglayer
    '''

    def __init__(self, stream_name = ''):

        self._name = stream_name if stream_name else 'tmsi'
        self._consumer = None
        self.device = None
        self._date = None
        self._outlet = None


    def open(self, device):
        '''
        Input is an open TMSiSDK device object
        '''

        print("LSLWriter-open")
        self.device = device

        try:
            self._date = datetime.now()
            self._sample_rate = device.config.sample_rate
            self._num_channels = len(device.channels)

            # Calculate nr of sample-sets within one sample-data-block:
            # This is the nr of sample-sets in 150 milli-seconds or when the
            # sample-data-block-size exceeds 64kb the it will become the nr of
            # sample-sets that fit in 64kb
            self._num_sample_sets_per_sample_data_block = int(self._sample_rate * 0.15)
            size_one_sample_set = len(self.device.channels) * 4
            if ((self._num_sample_sets_per_sample_data_block * size_one_sample_set) > 64000):
                self._num_sample_sets_per_sample_data_block = int(64000 / size_one_sample_set)

            # provide LSL with metadata
            info = StreamInfo(
                self._name,
                'EEG',
                self._num_channels,
                self._sample_rate,
                'float32',
                'tmsi-' + str(self.device.id)
                )
            chns = info.desc().append_child("channels")
            for idx, ch in enumerate(self.device.channels): # active channels
                 chn = chns.append_child("channel")
                 chn.append_child_value("label", ch.name)
                 chn.append_child_value("unit", ch.unit_name)
                 chn.append_child_value("type", str(ch.type).replace('ChannelType.', ''))
            info.desc().append_child_value("manufacturer", "TMSi")

            # start sampling data and pushing to LSL
            self._outlet = StreamOutlet(info, self._num_sample_sets_per_sample_data_block)
            self._consumer = LSLConsumer(self._outlet)
            sample_data_server.registerConsumer(self.device.id, self._consumer)

        except:
            raise TMSiError(TMSiErrorCode.file_writer_error)


    def close(self):

        print("LSLWriter-close")
        sample_data_server.unregisterConsumer(self._consumer)
        # let garbage collector take care of destroying LSL outlet
        self._consumer = None
        self._outlet = None
