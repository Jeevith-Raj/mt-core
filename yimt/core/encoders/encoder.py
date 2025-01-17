"""Base class for encoders and generic multi encoders."""

import abc
import itertools

import tensorflow as tf

from yimt.core.layers.reducer import JoinReducer


class Encoder(tf.keras.layers.Layer):
    """Base class for encoders."""

    def build_mask(self, inputs, sequence_length=None, dtype=tf.bool):
        """Builds a boolean mask for :obj:`inputs`."""
        if sequence_length is None:
            return None
        return tf.sequence_mask(
            sequence_length, maxlen=tf.shape(inputs)[1], dtype=dtype
        )

    @abc.abstractmethod
    def call(self, inputs, sequence_length=None, training=None):
        """Encodes an input sequence.

        Args:
          inputs: The inputs to encode of shape :math:`[B, T, ...]`.
          sequence_length: The length of each input with shape :math:`[B]`.
          training: Run in training mode.

        Returns:
          A tuple ``(outputs, sequence_length)``.
        """
        raise NotImplementedError()

    def __call__(self, inputs, sequence_length=None, **kwargs):
        """Encodes an input sequence.

        Args:
          inputs: A 3D ``tf.Tensor`` or ``tf.RaggedTensor``.
          sequence_length: A 1D ``tf.Tensor`` (optional if :obj:`inputs` is a
            ``tf.RaggedTensor``).
          training: Run the encoder in training mode.

        Returns:
          If :obj:`inputs` is a ``tf.Tensor``, the encoder returns a tuple
          ``(outputs, sequence_length)``. If :obj:`inputs` is a
          ``tf.RaggedTensor``, the encoder returns a tuple ``(outputs, state)``,
          where ``outputs`` is a ``tf.RaggedTensor``.
        """
        if isinstance(inputs, tf.RaggedTensor):
            is_ragged = True
            inputs, sequence_length = inputs.to_tensor(), inputs.row_lengths()
        else:
            is_ragged = False
        outputs, sequence_length = super().__call__(
            inputs, sequence_length=sequence_length, **kwargs
        )
        if is_ragged:
            outputs = tf.RaggedTensor.from_tensor(outputs, lengths=sequence_length)
            return outputs
        else:
            return outputs, sequence_length


class ParallelEncoder(Encoder):
    """An encoder that encodes its input with several encoders and reduces the
    outputs and states together. Additional layers can be applied on each encoder
    output and on the combined output (e.g. to layer normalize each encoder
    output).

    If the input is a single ``tf.Tensor``, the same input will be encoded by
    every encoders. Otherwise, when the input is a Python sequence (e.g. the non
    reduced output of a :class:`yimt.inputters.ParallelInputter`),
    each encoder will encode its corresponding input in the sequence.

    See for example "Multi-Columnn Encoder" in https://arxiv.org/abs/1804.09849.
    """

    def __init__(
        self,
        encoders,
        outputs_reducer=None,
        outputs_layer_fn=None,
        combined_output_layer_fn=None,
    ):
        """Initializes the parameters of the encoder.

        Args:
          encoders: A list of :class:`yimt.encoders.Encoder` or a single
            one, in which case the same encoder is applied to each input.
          outputs_reducer: A :class:`yimt.layers.Reducer` to merge all
            outputs. If ``None``, defaults to
            :class:`yimt.layers.JoinReducer`.
          outputs_layer_fn: A callable or list of callables applied to the
            encoders outputs If it is a single callable, it is applied on each
            encoder output. Otherwise, the ``i`` th callable is applied on
            encoder ``i`` output.
          combined_output_layer_fn: A callable to apply on the combined output
            (i.e. the output of :obj:`outputs_reducer`).

        Raises:
          ValueError: if :obj:`outputs_layer_fn` is a list with a size not equal
            to the number of encoders.
        """
        if (
            isinstance(encoders, list)
            and outputs_layer_fn is not None
            and isinstance(outputs_layer_fn, list)
            and len(outputs_layer_fn) != len(encoders)
        ):
            raise ValueError(
                "The number of output layers must match the number of encoders; "
                "expected %d layers but got %d."
                % (len(encoders), len(outputs_layer_fn))
            )
        super().__init__()
        self.encoders = encoders
        self.outputs_reducer = (
            outputs_reducer if outputs_reducer is not None else JoinReducer()
        )
        self.outputs_layer_fn = outputs_layer_fn
        self.combined_output_layer_fn = combined_output_layer_fn

    def call(self, inputs, sequence_length=None, training=None):
        all_outputs = []
        all_sequence_lengths = []
        parallel_inputs = isinstance(inputs, (list, tuple))
        parallel_encoders = isinstance(self.encoders, (list, tuple))

        if parallel_encoders and parallel_inputs and len(inputs) != len(self.encoders):
            raise ValueError(
                "ParallelEncoder expects as many inputs as parallel encoders"
            )
        if parallel_encoders:
            encoders = self.encoders
        else:
            encoders = itertools.repeat(
                self.encoders, len(inputs) if parallel_inputs else 1
            )

        for i, encoder in enumerate(encoders):
            if parallel_inputs:
                encoder_inputs = inputs[i]
                length = sequence_length[i]
            else:
                encoder_inputs = inputs
                length = sequence_length

            outputs, length = encoder(
                encoder_inputs, sequence_length=length, training=training
            )

            if self.outputs_layer_fn is not None:
                if isinstance(self.outputs_layer_fn, list):
                    outputs = self.outputs_layer_fn[i](outputs)
                else:
                    outputs = self.outputs_layer_fn(outputs)

            all_outputs.append(outputs)
            all_sequence_lengths.append(length)

        outputs, sequence_length = self.outputs_reducer(
            all_outputs, sequence_length=all_sequence_lengths
        )

        if self.combined_output_layer_fn is not None:
            outputs = self.combined_output_layer_fn(outputs)

        return (outputs, sequence_length)
