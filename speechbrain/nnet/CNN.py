"""Library implementing convolutional neural networks.

Author
    Mirco Ravanelli 2020
"""

import math
import torch
import logging
import numpy as np
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger(__name__)


class Conv(nn.Module):
    """This function implements 1D, 2D, and sinc_conv (SincNet) convolutionals.

    This class implements convolutional layers:
    Conv1d is used when the specified kernel size is 1d (e.g, kernel_size=3).
    Conv2d is used when the specified kernel size is 2d (e.g, kernel_size=3,5).
    sinc_conv (SincNet) is used when sinc_conv is True.

    Arguments
    ---------
    out_channels: int
        It is the number of output channels.
    kernel_size: int
        It is a list containing the size of the kernels. For 1D convolutions,
        the list contains a single integer (convolution over the time axis),
        while for 2D convolutions the list is composed of two
        values (i.e, time and frequency kernel sizes respectively).
    stride: int
        it is a list containing the stride factors. For 1D convolutions, the
        list contains a single integer (stride over the time axis), while
        for 2D convolutions the list is composed of two values (i.e, time and
        frequency kernel sizes, respectively). When the stride factor > 1, a
        decimation (in the time or frequnecy domain) is implicitly performed.
    dilation: int
        it is a list containing the dilation factors. For 1D convolutions, the
        list contains a single integer (dilation over the time axis), while
        for 2D convolutions the list is composed of two values (i.e, time and
        frequency kernel sizes, respectively).
    padding: bool
        if True, zero-padding is performed.
    padding_mode: str
        This flag specifies the type of padding. See torch.nn documentation
        for more information.
    groups: int
        This option specifies the convolutional groups. See torch.nn
        documentation for more information.
    bias: bool
        If True, the additive bias b is adopted.
    sinc_conv: bool
        If True computes convolution with sinc-based filters (SincNet).
    sample_rate: int,
        Sampling rate of the input signals. It is only used for sinc_conv.
    min_low_hz: float
        Lowest possible frequency (in Hz) for a filter. It is only used for
        sinc_conv.
    min_low_hz: float
        Lowest possible value (in Hz) for a filter bandwidth.

    Example
    -------
    >>> inp_tensor = torch.rand([10, 16000, 1])
    >>> cnn_1d = Conv(out_channels=25, kernel_size=(11,))
    >>> out_tensor = cnn_1d(inp_tensor, init_params=True)
    >>> out_tensor.shape
    torch.Size([10, 16000, 25])
    >>> inp_tensor = torch.rand([10, 16000, 1])
    >>> cnn_1d = Conv(out_channels=25, kernel_size=11)
    >>> out_tensor = cnn_1d(inp_tensor, init_params=True)
    >>> out_tensor.shape
    torch.Size([10, 16000, 25])
    >>> inp_tensor = torch.rand([10, 100, 40, 128])
    >>> cnn_2d = Conv(out_channels=25, kernel_size=(11,5))
    >>> out_tensor = cnn_2d(inp_tensor, init_params=True)
    >>> out_tensor.shape
    torch.Size([10, 100, 40, 25])
    >>> inp_tensor = torch.rand([10, 4000])
    >>> sinc_conv = Conv(out_channels=8, kernel_size=(129,), sinc_conv=True)
    >>> out_tensor = sinc_conv(inp_tensor, init_params=True)
    >>> out_tensor.shape
    torch.Size([10, 4000, 8])
    """

    def __init__(
        self,
        out_channels,
        kernel_size,
        stride=(1, 1),
        dilation=(1, 1),
        padding=True,
        groups=1,
        bias=True,
        padding_mode="reflect",
        sinc_conv=False,
        sample_rate=16000,
        min_low_hz=50,
        min_band_hz=50,
    ):
        super().__init__()
        self.out_channels = out_channels
        self.kernel_size = kernel_size
        self.stride = stride
        self.dilation = dilation
        self.padding = padding
        self.groups = groups
        self.bias = bias
        self.padding_mode = padding_mode
        self.sinc_conv = sinc_conv
        self.sample_rate = sample_rate
        self.min_low_hz = min_low_hz
        self.min_band_hz = min_band_hz
        self.reshape_conv1d = False
        self.unsqueeze = False

        # Check the specified kernel (to decide between conv1d and conv2d)
        self._kernel_check()

    def _kernel_check(self):
        """Checks the specified kernel and decides if we have to use conv1d,
        conv2d, or sinc_conv.
        """

        self.conv1d = False
        self.conv2d = False

        if isinstance(self.kernel_size, int):
            self.kernel_size = (self.kernel_size,)

        # Make sure kernel_size is odd (needed for padding)
        for size in self.kernel_size:
            if size % 2 == 0:
                raise ValueError(
                    "The field kernel size must be an odd number. Got %s."
                    % (self.kernel_size)
                )

        if len(self.kernel_size) == 1:
            self.conv1d = True

        if len(self.kernel_size) == 2:
            self.conv2d = True

        if self.sinc_conv and self.conv2d:
            raise ValueError(
                "sinc_conv expects 1d kernels. Got " + len(self.kernel_size)
            )

    def init_params(self, first_input):
        """
        Initializes the parameters of the convolutional layer.

        Arguments
        ---------
        first_input : tensor
            A first input used for initializing the parameters.
        """
        self.device = first_input.device

        if self.conv1d:
            if self.sinc_conv:
                self._init_sinc_conv(first_input)
            else:
                self._init_conv1d(first_input)

        if self.conv2d:
            self._init_conv2d(first_input)

    def _init_conv1d(self, first_input):
        """
        Initializes the parameters of the conv1d layer.

        Arguments
        ---------
        first_input : tensor
            A first input used for initializing the parameters.
        """
        if len(first_input.shape) == 1:
            raise ValueError(
                "conv1d expects 2d, 3d, or 4d inputs. Got "
                + len(first_input.shape)
            )

        if len(first_input.shape) == 2:
            self.unsqueeze = True
            self.in_channels = 1

        if len(first_input.shape) == 3:
            self.in_channels = first_input.shape[2]

        if len(first_input.shape) == 4:
            self.reshape_conv1d = True
            self.in_channels = first_input.shape[2] * first_input.shape[3]

        if len(first_input.shape) > 4:
            raise ValueError(
                "conv1d expects 2d, 3d, or 4d inputs. Got " + len(first_input)
            )

        self.conv = nn.Conv1d(
            self.in_channels,
            self.out_channels,
            self.kernel_size[0],
            stride=self.stride[0],
            dilation=self.dilation[0],
            padding=0,
            groups=self.groups,
            bias=self.bias,
        ).to(first_input.device)

    def _init_conv2d(self, first_input):
        """
        Initializes the parameters of the conv2d layer.

        Arguments
        ---------
        first_input : tensor
            A first input used for initializing the parameters.
        """
        if len(first_input.shape) <= 2:
            raise ValueError(
                "conv2d expects 3d or 4d inputs. Got " + len(first_input.shape)
            )

        if len(first_input.shape) == 3:
            self.unsqueeze = True
            self.in_channels = 1

        if len(first_input.shape) == 4:
            self.in_channels = first_input.shape[3]

        if len(first_input.shape) > 4:
            raise ValueError(
                "conv1d expects 3d or 4d inputs. Got " + len(first_input)
            )

        self.kernel_size = (self.kernel_size[1], self.kernel_size[0])
        self.stride = (self.stride[1], self.stride[0])
        self.dilation = (self.dilation[1], self.dilation[0])

        self.conv = nn.Conv2d(
            self.in_channels,
            self.out_channels,
            self.kernel_size,
            stride=self.stride,
            padding=0,
            dilation=self.dilation,
            groups=self.groups,
            bias=self.bias,
        ).to(first_input.device)

    def _init_sinc_conv(self, first_input):
        """
        Initializes the parameters of the sinc_conv layer.

        Arguments
        ---------
        first_input : tensor
            A first input used for initializing the parameters.
        """
        self._init_conv1d(first_input)

        # Initialize filterbanks such that they are equally spaced in Mel scale
        high_hz = self.sample_rate / 2 - (self.min_low_hz + self.min_band_hz)

        mel = torch.linspace(
            self._to_mel(self.min_low_hz),
            self._to_mel(high_hz),
            self.out_channels + 1,
        )

        hz = self._to_hz(mel)

        # Filter lower frequency and bands
        self.low_hz_ = hz[:-1].unsqueeze(1)
        self.band_hz_ = (hz[1:] - hz[:-1]).unsqueeze(1)

        # Maiking freq and bands learnable
        self.low_hz_ = nn.Parameter(self.low_hz_).to(self.device)
        self.band_hz_ = nn.Parameter(self.band_hz_).to(self.device)

        # Hamming window
        n_lin = torch.linspace(
            0,
            (self.kernel_size[0] / 2) - 1,
            steps=int((self.kernel_size[0] / 2)),
        )
        self.window_ = 0.54 - 0.46 * torch.cos(
            2 * math.pi * n_lin / self.kernel_size[0]
        ).to(self.device)

        # Time axis  (only half is needed due to symmetry)
        n = (self.kernel_size[0] - 1) / 2.0
        self.n_ = (
            2 * math.pi * torch.arange(-n, 0).view(1, -1) / self.sample_rate
        ).to(self.device)

    def forward(self, x, init_params=False):
        """Returns the output of the convolution.

        Arguments
        ---------
        x : torch.Tensor
        """
        if init_params:
            self.init_params(x)

        x = x.transpose(1, -1)

        if self.reshape_conv1d:
            or_shape = x.shape
            x = x.reshape(or_shape[0], or_shape[1] * or_shape[2], or_shape[3])

        if self.unsqueeze:
            x = x.unsqueeze(1)

        if self.padding:
            x = self._manage_padding(
                x, self.kernel_size, self.dilation, self.stride
            )
        if self.sinc_conv:
            sinc_filters = self._get_sinc_filters()

            wx = F.conv1d(
                x,
                sinc_filters,
                stride=self.stride[0],
                padding=0,
                dilation=self.dilation[0],
                bias=None,
                groups=1,
            )

        else:
            wx = self.conv(x)

        # Retrieving the original shapes
        if self.unsqueeze:
            wx = wx.squeeze(1)

        if self.reshape_conv1d:
            wx = wx.reshape(or_shape[0], wx.shape[1], wx.shape[2], wx.shape[3])

        wx = wx.transpose(1, -1)

        return wx

    def _manage_padding(self, x, kernel_size, dilation, stride):
        """This function performs zero-padding on the time and frequency axis
        such that their lengths is unchanged after the convolution.

        Arguments
        ---------
        x : torch.Tensor
        kernel_size : int
        dilation : int
        stride: int
        """

        # Detecting input shape
        L_in = x.shape[-1]

        # Time padding
        padding = self._get_padding_elem(
            L_in, stride[-1], kernel_size[-1], dilation[-1]
        )

        if self.conv2d:
            padding_freq = self._get_padding_elem(
                L_in, stride[-2], kernel_size[-2], dilation[-2]
            )
            padding = padding + padding_freq

        # Applying padding
        x = nn.functional.pad(x, tuple(padding), mode=self.padding_mode)

        return x

    def _get_sinc_filters(self,):
        """This functions creates the sinc-filters to used for sinc-conv.
        """
        # Computing the low frequencies of the filters
        low = self.min_low_hz + torch.abs(self.low_hz_)

        # Setting minimum band and minimum freq
        high = torch.clamp(
            low + self.min_band_hz + torch.abs(self.band_hz_),
            self.min_low_hz,
            self.sample_rate / 2,
        )
        band = (high - low)[:, 0]

        # Passing from n_ to the corresponding f_times_t domain
        f_times_t_low = torch.matmul(low, self.n_)
        f_times_t_high = torch.matmul(high, self.n_)

        # Left part of the filters.
        band_pass_left = (
            (torch.sin(f_times_t_high) - torch.sin(f_times_t_low))
            / (self.n_ / 2)
        ) * self.window_

        # Central element of the filter
        band_pass_center = 2 * band.view(-1, 1)

        # Right part of the filter (sinc filters are symmetric)
        band_pass_right = torch.flip(band_pass_left, dims=[1])

        # Combining left, central, and right part of the filter
        band_pass = torch.cat(
            [band_pass_left, band_pass_center, band_pass_right], dim=1
        )

        # Amplitude normalization
        band_pass = band_pass / (2 * band[:, None])

        # Setting up the filter coefficients
        filters = (
            (band_pass)
            .view(self.out_channels, 1, self.kernel_size[0])
            .to(self.device)
        )

        return filters

    @staticmethod
    def _get_padding_elem(L_in, stride, kernel_size, dilation):
        """This computes the number of elements to add for zero-padding.

        Arguments
        ---------
        L_in : int
        stride: int
        kernel_size : int
        dilation : int
        """
        if stride > 1:
            n_steps = math.ceil(((L_in - kernel_size * dilation) / stride) + 1)
            L_out = stride * (n_steps - 1) + kernel_size * dilation
            padding = [kernel_size // 2, kernel_size // 2]

        else:
            L_out = (L_in - dilation * (kernel_size - 1) - 1) / stride + 1
            L_out = int(L_out)

            padding = [(L_in - L_out) // 2, (L_in - L_out) // 2]
        return padding

    @staticmethod
    def _to_mel(hz):
        """Converts frequency in Hz to the mel scale.
        """
        return 2595 * np.log10(1 + hz / 700)

    @staticmethod
    def _to_hz(mel):
        """Converts frequency in the mel scale to Hz.
        """
        return 700 * (10 ** (mel / 2595) - 1)