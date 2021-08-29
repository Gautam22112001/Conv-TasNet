import os
import random

import torch
import torchaudio
import torch.nn.functional as F

from dataset import SpectrogramDataset

__sources__ = ['drums', 'bass', 'other', 'vocals']

SAMPLE_RATE_MUSDB18 = 44100
EPS = 1e-12
THRESHOLD_POWER = 1e-5
MINSCALE = 0.75
MAXSCALE = 1.25

class SpectrogramTrainDataset(SpectrogramDataset):
    """
    Training dataset that returns randomly selected mixture spectrograms.
    In accordane with "D3Net: Densely connected multidilated DenseNet for music source separation," training dataset includes all 100 songs.
    """
    def __init__(self, musdb18_root, fft_size, hop_size=None, window_fn='hann', normalize=False, sr=SAMPLE_RATE_MUSDB18, patch_samples=4*SAMPLE_RATE_MUSDB18, overlap=None, samples_per_epoch=None, sources=__sources__, target=None, augmentation=True):
        super().__init__(musdb18_root, fft_size=fft_size, hop_size=hop_size, window_fn=window_fn, normalize=normalize, sr=sr, sources=sources, target=target)
        
        train_txt_path = os.path.join(musdb18_root, 'train.txt')

        with open(train_txt_path, 'r') as f:
            names = [line.strip() for line in f]
        
        self.patch_samples = patch_samples

        self.augmentation = augmentation

        self.tracks = []

        if augmentation:
            if samples_per_epoch is None:
                patch_duration = patch_samples / sr
                total_duration = 0

                for songID, name in enumerate(names):
                    mixture_path = os.path.join(musdb18_root, 'train', name, "mixture.wav")
                    wave, sr = torchaudio.load(mixture_path)
                    track_samples = wave.size(1)

                    track = {
                        'name': name,
                        'samples': track_samples,
                        'path': {
                            'mixture': mixture_path
                        }
                    }
                    
                    for source in sources:
                        track['path'][source] = os.path.join(musdb18_root, 'train', name, "{}.wav".format(source))
                    
                    self.tracks.append(track)

                    track_duration = track_samples / sr
                    total_duration += track_duration

                samples_per_epoch = int(total_duration / patch_duration)

            self.samples_per_epoch = samples_per_epoch
            self.json_data = None
        else:
            if overlap is None:
                overlap = patch_samples // 2
            
            self.samples_per_epoch = None

            for songID, name in enumerate(names):
                mixture_path = os.path.join(musdb18_root, 'train', name, "mixture.wav")
                wave, sr = torchaudio.load(mixture_path)
                track_samples = wave.size(1)

                track = {
                    'name': name,
                    'samples': track_samples,
                    'path': {
                        'mixture': mixture_path
                    }
                }

                for source in sources:
                    track['path'][source] = os.path.join(musdb18_root, 'train', name, "{}.wav".format(source))
                
                self.tracks.append(track)

                for start in range(0, track_samples, patch_samples - overlap):
                    if start + patch_samples >= track_samples:
                        break
                    data = {
                        'songID': songID,
                        'start': start,
                        'samples': patch_samples,
                    }
                    self.json_data.append(data)
    
    def __getitem__(self, idx):
        """
        Returns:
            mixture <torch.Tensor>: Complex tensor with shape (1, n_mics, n_bins, n_frames)  if `target` is list, otherwise (n_mics, n_bins, n_frames) 
            target <torch.Tensor>: Complex tensor with shape (len(target), n_mics, n_bins, n_frames) if `target` is list, otherwise (n_mics, n_bins, n_frames)
        """
        if self.augmentation:
            mixture, target = self._getitem_augmentation()
        else:
            mixture, target = self._getitem(idx)
        
        n_dims = mixture.dim()

        if n_dims > 2:
            mixture_channels = mixture.size()[:-1]
            target_channels = target.size()[:-1]
            mixture = mixture.reshape(-1, mixture.size(-1))
            target = target.reshape(-1, target.size(-1))

        mixture = torch.stft(mixture, n_fft=self.fft_size, hop_length=self.hop_size, window=self.window, normalized=self.normalize, return_complex=True) # (1, n_mics, n_bins, n_frames) or (n_mics, n_bins, n_frames)
        target = torch.stft(target, n_fft=self.fft_size, hop_length=self.hop_size, window=self.window, normalized=self.normalize, return_complex=True) # (len(sources), n_mics, n_bins, n_frames) or (n_mics, n_bins, n_frames)
        
        if n_dims > 2:
            mixture = mixture.reshape(*mixture_channels, *mixture.size()[-2:])
            target = target.reshape(*target_channels, *target.size()[-2:])

        return mixture, target
    
    def __len__(self):
        if self.augmentation:
            return self.samples_per_epoch
        else:
            source = self.sources[0]
            
            return len(self.json_data[source])

    def _getitem(self, idx):
        """
        Returns time domain signals
        Args:
            idx <int>: index
        Returns:
            mixture <torch.Tensor>: (1, n_mics, T) if `target` is list, otherwise (n_mics, T)
            target <torch.Tensor>: (len(target), n_mics, T) if `target` is list, otherwise (n_mics, T)
        """
        mixture, target, _, _ = super().__getitem__(idx)

        return mixture, target
    
    def _getitem_augmentation(self):
        """
        Returns time domain signals
        Returns:
            mixture <torch.Tensor>: (1, n_mics, T) if `target` is list, otherwise (n_mics, T)
            target <torch.Tensor>: (len(target), n_mics, T) if `target` is list, otherwise (n_mics, T)
            name <str>: Artist and title of song
        """
        n_songs = len(self.tracks)
        song_indices = random.choices(range(n_songs), k=len(self.sources))

        sources = []

        for _source, songID in zip(self.sources, song_indices):
            track = self.tracks[songID]
            source_path = track['path'][_source]
            track_samples = track['samples']

            start = random.randint(0, track_samples - self.patch_samples - 1)
            flip = random.choice([True, False])
            scale = random.uniform(MINSCALE, MAXSCALE)

            source, _ = torchaudio.load(source_path, frame_offset=start, num_frames=self.patch_samples)

            if flip:
                source = torch.flip(source, dims=(0,))

            sources.append(scale * source.unsqueeze(dim=0))
        
        if type(self.target) is list:
            target = []
            for _target in self.target:
                source_idx = self.sources.index(_target)
                _target = sources[source_idx]
                target.append(_target)
            target = torch.cat(target, dim=0)

            sources = torch.cat(sources, dim=0)
            mixture = sources.sum(dim=0, keepdim=True)
        else:
            source_idx = self.sources.index(self.target)
            target = sources[source_idx]
            target = target.squeeze(dim=0)

            sources = torch.cat(sources, dim=0)
            mixture = sources.sum(dim=0)

        return mixture, target

class SpectrogramEvalDataset(SpectrogramDataset):
    def __init__(self, musdb18_root, fft_size, hop_size=None, window_fn='hann', normalize=False, sr=SAMPLE_RATE_MUSDB18, patch_samples=10*SAMPLE_RATE_MUSDB18, max_samples=None, sources=__sources__, target=None):
        super().__init__(musdb18_root, fft_size=fft_size, hop_size=hop_size, window_fn=window_fn, normalize=normalize, sr=sr, sources=sources, target=target)
        
        assert_sample_rate(sr)

        valid_txt_path = os.path.join(musdb18_root, 'validation.txt')
        
        with open(valid_txt_path, 'r') as f:
            names = [line.strip() for line in f]

        self.patch_samples = patch_samples
        self.max_samples = max_samples

        self.tracks = []
        self.json_data = []

        for songID, name in enumerate(names):
            mixture_path = os.path.join(musdb18_root, 'train', name, "mixture.wav")
            wave, sr = torchaudio.load(mixture_path)
            track_samples = wave.size(1)

            track = {
                'name': name,
                'samples': track_samples,
                'path': {
                    'mixture': mixture_path
                }
            }

            for source in sources:
                track['path'][source] = os.path.join(musdb18_root, 'train', name, "{}.wav".format(source))

            song_data = {
                'songID': songID,
                'patches': []
            }

            max_samples = min(track_samples, self.max_samples)

            for start in range(0, max_samples, patch_samples):
                if start + patch_samples > max_samples:
                    data = {
                        'start': start,
                        'samples': max_samples - start,
                        'padding_start': 0,
                        'padding_end': start + patch_samples - max_samples
                    }
                else:
                    data = {
                        'start': start,
                        'samples': patch_samples,
                        'padding_start': 0,
                        'padding_end': 0
                    }
                song_data['patches'].append(data)

            self.json_data.append(song_data)
            self.tracks.append(track)

    def __getitem__(self, idx):
        """
        Returns:
            mixture <torch.Tensor>: Complex tensor with shape (1, n_mics, n_bins, n_frames)  if `target` is list, otherwise (n_mics, n_bins, n_frames) 
            target <torch.Tensor>: Complex tensor with shape (len(target), n_mics, n_bins, n_frames) if `target` is list, otherwise (n_mics, n_bins, n_frames)
            name <str>: Artist and title of song
        """
        song_data = self.json_data[idx]

        songID = song_data['songID']
        track = self.tracks[songID]
        name = track['name']
        paths = track['path']

        batch_mixture, batch_target = [], []
        max_samples = 0

        for data in song_data['patches']:
            start = data['start']
            samples = data['samples']

            if set(self.sources) == set(__sources__):
                
                mixture, _ = torchaudio.load(paths['mixture'], frame_offset=start, num_frames=samples)
            else:
                sources = []
                for _source in self.sources:
                    source, _ = torchaudio.load(paths[_source], frame_offset=start, num_frames=samples)
                    sources.append(source.unsqueeze(dim=0))
                sources = torch.cat(sources, dim=0)
                mixture = sources.sum(dim=0)
            
            if type(self.target) is list:
                target = []
                for _target in self.target:
                    source, _ = torchaudio.load(paths[_target], frame_offset=start, num_frames=samples)
                    target.append(source.unsqueeze(dim=0))
                target = torch.cat(target, dim=0)
                mixture = mixture.unsqueeze(dim=0)
            else:
                target, _ = torchaudio.load(paths[self.target], frame_offset=start, num_frames=samples)

            max_samples = max(max_samples, mixture.size(-1))

            batch_mixture.append(mixture)
            batch_target.append(target)
        
        batch_mixture_padded, batch_target_padded = [], []

        for mixture, target in zip(batch_mixture, batch_target):
            if mixture.size(-1) < max_samples:
                padding = max_samples - mixture.size(-1)
                mixture = F.pad(mixture, (0, padding))
                target = F.pad(target, (0, padding))
            batch_mixture_padded.append(mixture.unsqueeze(dim=0))
            batch_target_padded.append(target.unsqueeze(dim=0))

        batch_mixture = torch.cat(batch_mixture_padded, dim=0)
        batch_target = torch.cat(batch_target_padded, dim=0)

        n_dims = batch_mixture.dim()

        if n_dims > 2:
            mixture_channels = batch_mixture.size()[:-1]
            target_channels = batch_target.size()[:-1]
            batch_mixture = batch_mixture.reshape(-1, batch_mixture.size(-1))
            batch_target = batch_target.reshape(-1, batch_target.size(-1))

        batch_mixture = torch.stft(batch_mixture, n_fft=self.fft_size, hop_length=self.hop_size, window=self.window, normalized=self.normalize, return_complex=True) # (1, n_mics, n_bins, n_frames) or (n_mics, n_bins, n_frames)
        batch_target = torch.stft(batch_target, n_fft=self.fft_size, hop_length=self.hop_size, window=self.window, normalized=self.normalize, return_complex=True) # (len(sources), n_mics, n_bins, n_frames) or (n_mics, n_bins, n_frames)
        
        if n_dims > 2:
            batch_mixture = batch_mixture.reshape(*mixture_channels, *batch_mixture.size()[-2:])
            batch_target = batch_target.reshape(*target_channels, *batch_target.size()[-2:])
        
        return batch_mixture, batch_target, name

class SpectrogramTestDataset(SpectrogramDataset):
    def __init__(self, musdb18_root, fft_size, hop_size=None, window_fn='hann', normalize=False, sr=SAMPLE_RATE_MUSDB18, patch_samples=5*SAMPLE_RATE_MUSDB18, sources=__sources__, target=None):
        super().__init__(musdb18_root, fft_size=fft_size, hop_size=hop_size, window_fn=window_fn, normalize=normalize, sr=sr, sources=sources, target=target)

        assert_sample_rate(sr)

        test_txt_path = os.path.join(musdb18_root, 'test.txt')

        names = []
        with open(test_txt_path, 'r') as f:
            for line in f:
                name = line.strip()
                names.append(name)
        
        self.patch_samples = patch_samples

        self.tracks = []
        self.json_data = []

        for songID, name in enumerate(names):
            mixture_path = os.path.join(musdb18_root, 'test', name, "mixture.wav")
            wave, sr = torchaudio.load(mixture_path)
            track_samples = wave.size(1)

            track = {
                'name': name,
                'samples': track_samples,
                'path': {
                    'mixture': mixture_path
                }
            }

            for source in sources:
                track['path'][source] = os.path.join(musdb18_root, 'test', name, "{}.wav".format(source))

            song_data = {
                'songID': songID,
                'patches': []
            }

            for start in range(0, track_samples, patch_samples):
                if start + patch_samples > track_samples:
                    data = {
                        'start': start,
                        'samples': track_samples - start,
                        'padding_start': 0,
                        'padding_end': start + patch_samples - track_samples
                    }
                else:
                    data = {
                        'start': start,
                        'samples': patch_samples,
                        'padding_start': 0,
                        'padding_end': 0
                    }
                song_data['patches'].append(data)
            
            self.tracks.append(track)
            self.json_data.append(song_data)
        
    def __getitem__(self, idx):
        """
        Returns:
            mixture <torch.Tensor>: Complex tensor with shape (1, n_mics, n_bins, n_frames)  if `target` is list, otherwise (n_mics, n_bins, n_frames) 
            target <torch.Tensor>: Complex tensor with shape (len(target), n_mics, n_bins, n_frames) if `target` is list, otherwise (n_mics, n_bins, n_frames)
            samples <int>: Number of samples in time domain.
            name <str>: Artist and title of song
        """
        song_data = self.json_data[idx]
        patch_samples = self.patch_samples

        songID = song_data['songID']
        track = self.tracks[songID]
        name = track['name']
        paths = track['path']
        samples = track['samples']

        if set(self.sources) == set(__sources__):
            mixture, _ = torchaudio.load(paths['mixture']) # (n_mics, T)
        else:
            sources = []
            for _source in self.sources:
                source, _ = torchaudio.load(paths[_source]) # (n_mics, T)
                sources.append(source.unsqueeze(dim=0))
            sources = torch.cat(sources, dim=0) # (len(self.sources), n_mics, T)
            mixture = sources.sum(dim=0) # (n_mics, T)
        
        if type(self.target) is list:
            target = []
            for _target in self.target:
                source, _ = torchaudio.load(paths[_target]) # (n_mics, T)
                target.append(source.unsqueeze(dim=0))
            target = torch.cat(target, dim=0) # (len(target), n_mics, T)
            mixture = mixture.unsqueeze(dim=0) # (1, n_mics, T)
        else:
            # mixture: (n_mics, T)
            target, _ = torchaudio.load(paths[self.target]) # (n_mics, T)

        padding = (patch_samples - samples % patch_samples) % patch_samples

        mixture_padded, target_padded = F.pad(mixture, (0, padding)), F.pad(target, (0, padding))
        mixture_padded, target_padded = mixture_padded.reshape(*mixture_padded.size()[:-1], -1, patch_samples), target_padded.reshape(*target_padded.size()[:-1], -1, patch_samples)

        if type(self.target) is list:
            # mixture_padded: (1, n_mics, batch_size, patch_samples), target_padded: (len(target), n_mics, batch_size, patch_samples)
            # batch_mixture: (batch_size, 1, n_mics, patch_samples), batch_target: (batch_size, len(target), n_mics, patch_samples)
            batch_mixture, batch_target = mixture_padded.permute(2, 0, 1, 3).contiguous(), target_padded.permute(2, 0, 1, 3).contiguous()
        else:
            # mixture_padded: (n_mics, batch_size, patch_samples), target_padded: (n_mics, batch_size, patch_samples)
            # batch_mixture: (batch_size, n_mics, patch_samples), batch_target: (batch_size, n_mics, patch_samples)
            batch_mixture, batch_target = mixture_padded.permute(1, 0, 2).contiguous(), target_padded.permute(1, 0, 2).contiguous()

        n_dims = batch_mixture.dim()

        if n_dims > 2:
            mixture_channels = batch_mixture.size()[:-1]
            target_channels = batch_target.size()[:-1]
            batch_mixture = batch_mixture.reshape(-1, batch_mixture.size(-1))
            batch_target = batch_target.reshape(-1, batch_target.size(-1))

        batch_mixture = torch.stft(batch_mixture, n_fft=self.fft_size, hop_length=self.hop_size, window=self.window, normalized=self.normalize, return_complex=True) # (1, n_mics, n_bins, n_frames) or (n_mics, n_bins, n_frames)
        batch_target = torch.stft(batch_target, n_fft=self.fft_size, hop_length=self.hop_size, window=self.window, normalized=self.normalize, return_complex=True) # (len(sources), n_mics, n_bins, n_frames) or (n_mics, n_bins, n_frames)
        
        if n_dims > 2:
            batch_mixture = batch_mixture.reshape(*mixture_channels, *batch_mixture.size()[-2:])
            batch_target = batch_target.reshape(*target_channels, *batch_target.size()[-2:])
        
        return batch_mixture, batch_target, samples, name

"""
Data loader
"""
class EvalDataLoader(torch.utils.data.DataLoader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        assert self.batch_size == 1, "batch_size is expected 1, but given {}".format(self.batch_size)

        self.collate_fn = eval_collate_fn

class TestDataLoader(torch.utils.data.DataLoader):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        assert self.batch_size == 1, "batch_size is expected 1, but given {}".format(self.batch_size)

        self.collate_fn = test_collate_fn

def eval_collate_fn(batch):
    mixture, sources, name = batch[0]
    
    return mixture, sources, name

def test_collate_fn(batch):
    mixture, sources, samples, name = batch[0]
    
    return mixture, sources, samples, name

def assert_sample_rate(sr):
    assert sr == SAMPLE_RATE_MUSDB18, "sample rate is expected {}, but given {}".format(SAMPLE_RATE_MUSDB18, sr)