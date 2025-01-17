"""Define generic inputters."""

import abc

import tensorflow as tf

from yimt.core.data import dataset as dataset_util
from yimt.core.layers.reducer import JoinReducer
from yimt.core.utils import misc


class Inputter(tf.keras.layers.Layer):
    """Base class for inputters."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._asset_prefix = ""

    @property
    def asset_prefix(self):
        r"""The asset prefix is used to differentiate resources of parallel inputters.
        The most basic examples are the "source\_" and "target\_" prefixes.

        - When reading the data configuration, the inputter will read fields that
          start with this prefix (e.g. "source_vocabulary").
        - Assets exported by this inputter start with this prefix.
        """
        return self._asset_prefix

    @asset_prefix.setter
    def asset_prefix(self, asset_prefix):
        """Sets the asset prefix for this inputter."""
        self._asset_prefix = asset_prefix

    @property
    def num_outputs(self):
        """The number of parallel outputs produced by this inputter."""
        return 1

    def initialize(self, data_config):
        """Initializes the inputter.

        Args:
          data_config: A dictionary containing the data configuration set
            by the user.
        """
        _ = data_config
        return

    def export_assets(self, asset_dir):
        """Exports assets used by this tokenizer.

        Args:
          asset_dir: The directory where assets can be written.

        Returns:
          A dictionary containing additional assets used by the inputter.
        """
        _ = asset_dir
        return {}

    @abc.abstractmethod
    def make_dataset(self, data_file, training=None):
        """Creates the base dataset required by this inputter.

        Args:
          data_file: The data file.
          training: Run in training mode.

        Returns:
          A ``tf.data.Dataset`` instance or a list of ``tf.data.Dataset`` instances.
        """
        raise NotImplementedError()

    def get_dataset_size(self, data_file):
        """Returns the dataset size.

        If the inputter can efficiently compute the dataset size from a training
        file on disk, it can optionally override this method. Otherwise, we may
        compute the size later with a generic and slower approach (iterating over
        the dataset instance).

        Args:
          data_file: The data file.

        Returns:
          The dataset size or ``None``.
        """
        _ = data_file
        return None

    def make_inference_dataset(
        self,
        features_file,
        batch_size,
        batch_type="examples",
        length_bucket_width=None,
        num_threads=1,
        prefetch_buffer_size=None,
    ):
        """Builds a dataset to be used for inference.

        For evaluation and training datasets, see
        :class:`yimt.inputters.ExampleInputter`.

        Args:
          features_file: The test file.
          batch_size: The batch size to use.
          batch_type: The batching strategy to use: can be "examples" or "tokens".
          length_bucket_width: The width of the length buckets to select batch
            candidates from (for efficiency). Set ``None`` to not constrain batch
            formation.
          num_threads: The number of elements processed in parallel.
          prefetch_buffer_size: The number of batches to prefetch asynchronously. If
            ``None``, use an automatically tuned value.

        Returns:
          A ``tf.data.Dataset``.

        See Also:
          :func:`yimt.data.inference_pipeline`
        """
        transform_fns = _get_dataset_transforms(
            self, num_threads=num_threads, training=False
        )
        dataset = self.make_dataset(features_file, training=False)
        dataset = dataset.apply(
            dataset_util.inference_pipeline(
                batch_size,
                batch_type=batch_type,
                transform_fns=transform_fns,
                length_bucket_width=length_bucket_width,
                length_fn=self.get_length,
                num_threads=num_threads,
                prefetch_buffer_size=prefetch_buffer_size,
            )
        )
        return dataset

    @abc.abstractmethod
    def input_signature(self):
        """Returns the input signature of this inputter."""
        raise NotImplementedError()

    def get_length(self, features, ignore_special_tokens=False):
        """Returns the length of the input features, if defined.

        Args:
          features: The dictionary of input features.
          ignore_special_tokens: Ignore special tokens that were added by the
            inputter (e.g. <s> and/or </s>).

        Returns:
          The length.
        """
        _ = ignore_special_tokens
        return features.get("length")

    def get_padded_shapes(self, element_spec, maximum_length=None):
        """Returns the padded shapes for dataset elements.

        For example, this is used during batch size autotuning to pad all batches
        to the maximum sequence length.

        Args:
          element_spec: A nested structure of ``tf.TensorSpec``.
          maximum_length: Pad batches to this maximum length.

        Returns:
          A nested structure of ``tf.TensorShape``.
        """
        return tf.nest.map_structure(
            lambda spec: spec.shape
            if spec.shape.rank == 0
            else tf.TensorShape([maximum_length]).concatenate(spec.shape[1:]),
            element_spec,
        )

    def has_prepare_step(self):
        """Returns ``True`` if this inputter implements a data preparation step
        in method :meth:`yimt.inputters.Inputter.prepare_elements`.
        """
        return False

    def prepare_elements(self, elements, training=None):
        """Prepares dataset elements.

        This method is called on a batch of dataset elements. For example, it
        can be overriden to apply an external pre-tokenization.

        Note that the results of the method are unbatched and then passed to
        method :meth:`yimt.inputters.Inputter.make_features`.

        Args:
          elements: A batch of dataset elements.
          training: Run in training mode.

        Returns:
          A (possibly nested) structure of ``tf.Tensor``.
        """
        return elements

    @abc.abstractmethod
    def make_features(self, element=None, features=None, training=None):
        """Creates features from data.

        This is typically called in a data pipeline (such as ``Dataset.map``).
        Common transformation includes tokenization, parsing, vocabulary lookup,
        etc.

        This method accepts both a single :obj:`element` from the dataset or a
        partially built dictionary of :obj:`features`.

        Args:
          element: An element from the dataset returned by
            :meth:`yimt.inputters.Inputter.make_dataset`.
          features: An optional and possibly partial dictionary of features to
            augment.
          training: Run in training mode.

        Returns:
          A dictionary of ``tf.Tensor``.
        """
        raise NotImplementedError()

    def keep_for_training(self, features, maximum_length=None):
        """Returns ``True`` if this example should be kept for training.

        Args:
          features: A dictionary of ``tf.Tensor``.
          maximum_length: The maximum length used for training.

        Returns:
          A boolean.
        """
        length = self.get_length(features)
        if length is None:
            return True
        is_valid = tf.greater(length, 0)
        if maximum_length is not None:
            is_valid = tf.logical_and(is_valid, tf.less_equal(length, maximum_length))
        return is_valid

    def call(self, features, training=None):
        """Creates the model input from the features (e.g. word embeddings).

        Args:
          features: A dictionary of ``tf.Tensor``, the output of
            :meth:`yimt.inputters.Inputter.make_features`.
          training: Run in training mode.

        Returns:
          The model input.
        """
        _ = training
        return features


class MultiInputter(Inputter):
    """An inputter that gathers multiple inputters, possibly nested."""

    def __init__(self, inputters, reducer=None):
        if not isinstance(inputters, list) or not inputters:
            raise ValueError("inputters must be a non empty list")
        dtype = inputters[0].dtype
        for inputter in inputters:
            if inputter.dtype != dtype:
                raise TypeError("All inputters must have the same dtype")
        super().__init__(dtype=dtype)
        self.inputters = inputters
        self.reducer = reducer
        self.asset_prefix = ""  # Generate the default prefix for sub-inputters.

    @Inputter.asset_prefix.setter
    def asset_prefix(self, asset_prefix):
        self._asset_prefix = asset_prefix
        for i, inputter in enumerate(self.inputters):
            inputter.asset_prefix = "%s%d_" % (asset_prefix, i + 1)

    @property
    def num_outputs(self):
        if self.reducer is None or isinstance(self.reducer, JoinReducer):
            return len(self.inputters)
        return 1

    def get_leaf_inputters(self):
        """Returns a list of all leaf Inputter instances."""
        inputters = []
        for inputter in self.inputters:
            if isinstance(inputter, MultiInputter):
                inputters.extend(inputter.get_leaf_inputters())
            else:
                inputters.append(inputter)
        return inputters

    def __getattribute__(self, name):
        if name == "built":
            return all(inputter.built for inputter in self.inputters)
        else:
            return super().__getattribute__(name)

    def initialize(self, data_config):
        for inputter in self.inputters:
            inputter.initialize(
                misc.RelativeConfig(
                    data_config, inputter.asset_prefix, config_name="data"
                )
            )

    def export_assets(self, asset_dir):
        assets = {}
        for inputter in self.inputters:
            assets.update(inputter.export_assets(asset_dir))
        return assets

    def has_prepare_step(self):
        return any(inputter.has_prepare_step() for inputter in self.inputters)

    def prepare_elements(self, elements, training=None):
        return tuple(
            inputter.prepare_elements(elts)
            for inputter, elts in zip(self.inputters, elements)
        )


class ParallelInputter(MultiInputter):
    """A multi inputter that processes parallel data."""

    def __init__(
        self, inputters, reducer=None, share_parameters=False, combine_features=True
    ):
        """Initializes a parallel inputter.

        Args:
          inputters: A list of :class:`yimt.inputters.Inputter`.
          reducer: A :class:`yimt.layers.Reducer` to merge all inputs. If
            set, parallel inputs are assumed to have the same length.
          share_parameters: Share the inputters parameters.
          combine_features: Combine each inputter features in a single dict or
            return them separately. This is typically ``True`` for multi source
            inputs but ``False`` for features/labels parallel data.

        Raises:
          ValueError: if :obj:`share_parameters` is set but the child inputters are
            not of the same type.
        """
        super().__init__(inputters, reducer=reducer)
        self.combine_features = combine_features
        self.share_parameters = share_parameters
        if self.share_parameters:
            leaves = self.get_leaf_inputters()
            for inputter in leaves[1:]:
                if type(inputter) is not type(leaves[0]):  # noqa: E721
                    raise ValueError(
                        "Each inputter must be of the same type for parameter sharing"
                    )

    def _structure(self):
        """Returns the nested structure that represents this parallel inputter."""
        return [
            inputter._structure() if isinstance(inputter, ParallelInputter) else None
            for inputter in self.inputters
        ]

    def make_dataset(self, data_file, training=None):
        if not isinstance(data_file, list):
            data_file = [data_file]

        # For evaluation and inference, accept a flat list of data files for nested inputters.
        # This is needed when nesting can't easily be represented (e.g. on the command line).
        if not training:
            try:
                data_file = tf.nest.pack_sequence_as(
                    self._structure(), tf.nest.flatten(data_file)
                )
            except ValueError:
                data_file = []  # This will raise the error below.

        if len(data_file) != len(self.inputters):
            raise ValueError(
                "The number of data files must be the same as the number of inputters"
            )

        num_files = -1
        datasets = []
        for i, (inputter, data) in enumerate(zip(self.inputters, data_file)):
            dataset = inputter.make_dataset(data, training=training)
            if not isinstance(dataset, list):
                dataset = [dataset]
            datasets.append(dataset)
            if num_files < 0:
                num_files = len(dataset)
            elif len(dataset) != num_files:
                raise ValueError(
                    "All parallel inputs must have the same number of data files, "
                    "saw %d files for input 0 but got %d files for input %d"
                    % (num_files, len(dataset), i)
                )

        parallel_datasets = [
            tf.data.Dataset.zip(tuple(parallel_dataset))
            for parallel_dataset in zip(*datasets)
        ]
        if len(parallel_datasets) == 1:
            return parallel_datasets[0]
        if not training:
            raise ValueError("Only training data can be configured to multiple files")
        return parallel_datasets

    def get_dataset_size(self, data_file):
        common_size = None
        for inputter, data in zip(self.inputters, data_file):
            size = inputter.get_dataset_size(data)
            if size is not None:
                if common_size is None:
                    common_size = size
                elif size != common_size:
                    raise RuntimeError("Parallel datasets do not have the same size")
        return common_size

    def input_signature(self):
        if self.combine_features:
            signature = {}
            for i, inputter in enumerate(self.inputters):
                for key, value in inputter.input_signature().items():
                    signature["{}_{}".format(key, i)] = value
            return signature
        else:
            return tuple(inputter.input_signature() for inputter in self.inputters)

    def _index_features(self, features, index):
        if self.combine_features:
            return misc.extract_prefixed_keys(features, "inputter_{}_".format(index))
        else:
            return features[index]

    def get_length(self, features, ignore_special_tokens=False):
        lengths = [
            inputter.get_length(
                self._index_features(features, i),
                ignore_special_tokens=ignore_special_tokens,
            )
            for i, inputter in enumerate(self.inputters)
        ]
        if self.reducer is None:
            return lengths
        else:
            return lengths[0]

    def get_padded_shapes(self, element_spec, maximum_length=None):
        if maximum_length is None:
            maximum_length = [None for _ in self.inputters]
        elif not isinstance(maximum_length, (list, tuple)) or len(
            maximum_length
        ) != len(self.inputters):
            raise ValueError(
                "A maximum length should be set for each parallel inputter"
            )
        if self.combine_features:
            shapes = {}
            for i, (inputter, length) in enumerate(zip(self.inputters, maximum_length)):
                prefix = "inputter_%d_" % i
                spec = misc.extract_prefixed_keys(element_spec, prefix)
                sub_shapes = inputter.get_padded_shapes(spec, maximum_length=length)
                for key, value in sub_shapes.items():
                    shapes["%s%s" % (prefix, key)] = value
            return shapes
        else:
            return type(element_spec)(
                inputter.get_padded_shapes(spec, maximum_length=length)
                for inputter, spec, length in zip(
                    self.inputters, element_spec, maximum_length
                )
            )

    def make_features(self, element=None, features=None, training=None):
        if self.combine_features:
            if features is None:
                features = {}
            for i, inputter in enumerate(self.inputters):
                prefix = "inputter_%d_" % i
                sub_features = misc.extract_prefixed_keys(features, prefix)
                if not sub_features:
                    # Also try to read the format produced by the serving features.
                    sub_features = misc.extract_suffixed_keys(features, "_%d" % i)
                sub_features = inputter.make_features(
                    element=element[i] if element is not None else None,
                    features=sub_features,
                    training=training,
                )
                for key, value in sub_features.items():
                    features["%s%s" % (prefix, key)] = value
            return features
        else:
            if features is None:
                features = [{} for _ in self.inputters]
            else:
                features = list(features)
            for i, inputter in enumerate(self.inputters):
                features[i] = inputter.make_features(
                    element=element[i] if element is not None else None,
                    features=features[i],
                    training=training,
                )
            return tuple(features)

    def keep_for_training(self, features, maximum_length=None):
        if not isinstance(maximum_length, list):
            maximum_length = [maximum_length]
        # Unset maximum lengths are set to None (i.e. no constraint).
        maximum_length += [None] * (len(self.inputters) - len(maximum_length))
        constraints = []
        for i, inputter in enumerate(self.inputters):
            keep = inputter.keep_for_training(
                self._index_features(features, i), maximum_length=maximum_length[i]
            )
            if isinstance(keep, bool):
                if not keep:
                    return False
                continue
            constraints.append(keep)
        if not constraints:
            return True
        return tf.reduce_all(constraints)

    def build(self, input_shape):
        if self.share_parameters:
            # When sharing parameters, build the first leaf inputter and then set
            # all attributes with parameters to the other inputters.
            leaves = self.get_leaf_inputters()
            first, others = leaves[0], leaves[1:]
            first.build(input_shape)
            for name, attr in first.__dict__.copy().items():
                if isinstance(attr, tf.Variable) or (
                    isinstance(attr, tf.Module) and attr.variables
                ):
                    for inputter in others:
                        setattr(inputter, name, attr)
                        inputter.built = True
        else:
            for inputter in self.inputters:
                inputter.build(input_shape)
        super().build(input_shape)

    def call(self, features, training=None):
        transformed = [
            inputter(self._index_features(features, i), training=training)
            for i, inputter in enumerate(self.inputters)
        ]
        if self.reducer is not None:
            transformed = self.reducer(transformed)
        return transformed


class ExampleInputterAdapter:
    """Extends an inputter with methods to build evaluation and training datasets."""

    def make_evaluation_dataset(
        self,
        features_file,
        labels_file,
        batch_size,
        batch_type="examples",
        length_bucket_width=None,
        num_threads=1,
        prefetch_buffer_size=None,
    ):
        """Builds a dataset to be used for evaluation.

        Args:
          features_file: The evaluation source file.
          labels_file: The evaluation target file.
          batch_size: The batch size to use.
          batch_type: The batching strategy to use: can be "examples" or "tokens".
          length_bucket_width: The width of the length buckets to select batch
            candidates from (for efficiency). Set ``None`` to not constrain batch
            formation.
          num_threads: The number of elements processed in parallel.
          prefetch_buffer_size: The number of batches to prefetch asynchronously. If
            ``None``, use an automatically tuned value.

        Returns:
          A ``tf.data.Dataset``.

        See Also:
          :func:`yimt.data.inference_pipeline`
        """
        if labels_file is not None:
            data_files = [features_file, labels_file]
            length_fn = [
                self.features_inputter.get_length,
                self.labels_inputter.get_length,
            ]
        else:
            data_files = features_file
            length_fn = self.get_length

        transform_fns = _get_dataset_transforms(
            self, num_threads=num_threads, training=False
        )
        dataset = self.make_dataset(data_files, training=False)
        dataset = dataset.apply(
            dataset_util.inference_pipeline(
                batch_size,
                batch_type=batch_type,
                transform_fns=transform_fns,
                length_bucket_width=length_bucket_width,
                length_fn=length_fn,
                num_threads=num_threads,
                prefetch_buffer_size=prefetch_buffer_size,
            )
        )
        return dataset

    def make_training_dataset(
        self,
        features_file,
        labels_file,
        batch_size,
        batch_type="examples",
        batch_multiplier=1,
        batch_size_multiple=1,
        shuffle_buffer_size=None,
        length_bucket_width=None,
        maximum_features_length=None,
        maximum_labels_length=None,
        single_pass=False,
        num_shards=1,
        shard_index=0,
        num_threads=4,
        prefetch_buffer_size=None,
        cardinality_multiple=1,
        weights=None,
        batch_autotune_mode=False,
    ):
        """Builds a dataset to be used for training. It supports the full training
        pipeline, including:

        * sharding
        * shuffling
        * filtering
        * bucketing
        * prefetching

        Args:
          features_file: The source file or a list of training source files.
          labels_file: The target file or a list of training target files.
          batch_size: The batch size to use.
          batch_type: The training batching strategy to use: can be "examples" or
            "tokens".
          batch_multiplier: The batch size multiplier to prepare splitting accross
             replicated graph parts.
          batch_size_multiple: When :obj:`batch_type` is "tokens", ensure that the
            resulting batch size is a multiple of this value.
          shuffle_buffer_size: The number of elements from which to sample.
          length_bucket_width: The width of the length buckets to select batch
            candidates from (for efficiency). Set ``None`` to not constrain batch
            formation.
          maximum_features_length: The maximum length or list of maximum lengths of
            the features sequence(s). ``None`` to not constrain the length.
          maximum_labels_length: The maximum length of the labels sequence.
            ``None`` to not constrain the length.
          single_pass: If ``True``, makes a single pass over the training data.
          num_shards: The number of data shards (usually the number of workers in a
            distributed setting).
          shard_index: The shard index this data pipeline should read from.
          num_threads: The number of elements processed in parallel.
          prefetch_buffer_size: The number of batches to prefetch asynchronously. If
            ``None``, use an automatically tuned value.
          cardinality_multiple: Ensure that the dataset cardinality is a multiple of
            this value when :obj:`single_pass` is ``True``.
          weights: An optional list of weights to create a weighted dataset out of
            multiple training files.
          batch_autotune_mode: When enabled, all batches are padded to the maximum
            sequence length.

        Returns:
          A ``tf.data.Dataset``.

        See Also:
          :func:`yimt.data.training_pipeline`
        """
        if labels_file is not None:
            data_files = [features_file, labels_file]
            maximum_length = [maximum_features_length, maximum_labels_length]
            features_length_fn = self.features_inputter.get_length
            labels_length_fn = self.labels_inputter.get_length
        else:
            data_files = features_file
            maximum_length = maximum_features_length
            features_length_fn = self.get_length
            labels_length_fn = None

        dataset = self.make_dataset(data_files, training=True)

        filter_fn = lambda *arg: (
            self.keep_for_training(
                misc.item_or_tuple(arg), maximum_length=maximum_length
            )
        )

        transform_fns = _get_dataset_transforms(
            self, num_threads=num_threads, training=True
        )
        transform_fns.append(lambda dataset: dataset.filter(filter_fn))

        if batch_autotune_mode:
            # In this mode we want to return batches where all sequences are padded
            # to the maximum possible length in order to maximize the memory usage.
            # Shuffling, sharding, prefetching, etc. are not applied since correctness and
            # performance are not important.

            if isinstance(dataset, list):  # Ignore weighted dataset.
                dataset = dataset[0]

            # We repeat the dataset now to ensure full batches are always returned.
            dataset = dataset.repeat()
            for transform_fn in transform_fns:
                dataset = dataset.apply(transform_fn)

            # length_fn returns the maximum length instead of the actual example length so
            # that batches are built as if each example has the maximum length.
            if labels_file is not None:
                constant_length_fn = [
                    lambda x: maximum_features_length,
                    lambda x: maximum_labels_length,
                ]
            else:
                constant_length_fn = lambda x: maximum_features_length

            # The length dimension is set to the maximum length in the padded shapes.
            padded_shapes = self.get_padded_shapes(
                dataset.element_spec, maximum_length=maximum_length
            )

            # Dynamically pad each sequence to the maximum length.
            def _pad_to_shape(tensor, padded_shape):
                if tensor.shape.rank == 0:
                    return tensor
                tensor_shape = misc.shape_list(tensor)
                paddings = [
                    [0, padded_dim - tensor_dim]
                    if tf.is_tensor(tensor_dim) and padded_dim is not None
                    else [0, 0]
                    for tensor_dim, padded_dim in zip(tensor_shape, padded_shape)
                ]
                return tf.pad(tensor, paddings)

            dataset = dataset.map(
                lambda *arg: tf.nest.map_structure(
                    _pad_to_shape, misc.item_or_tuple(arg), padded_shapes
                )
            )
            dataset = dataset.apply(
                dataset_util.batch_sequence_dataset(
                    batch_size,
                    batch_type=batch_type,
                    batch_multiplier=batch_multiplier,
                    length_bucket_width=1,
                    length_fn=constant_length_fn,
                )
            )
            return dataset

        if weights is not None:
            dataset = (dataset, weights)
        dataset = dataset_util.training_pipeline(
            batch_size,
            batch_type=batch_type,
            batch_multiplier=batch_multiplier,
            batch_size_multiple=batch_size_multiple,
            transform_fns=transform_fns,
            length_bucket_width=length_bucket_width,
            features_length_fn=features_length_fn,
            labels_length_fn=labels_length_fn,
            single_pass=single_pass,
            num_shards=num_shards,
            shard_index=shard_index,
            num_threads=num_threads,
            dataset_size=self.get_dataset_size(data_files),
            shuffle_buffer_size=shuffle_buffer_size,
            prefetch_buffer_size=prefetch_buffer_size,
            cardinality_multiple=cardinality_multiple,
        )(dataset)
        return dataset


def _register_example_weight(features, labels, weight):
    labels["weight"] = tf.strings.to_number(weight)
    return features, labels


class ExampleInputter(ParallelInputter, ExampleInputterAdapter):
    """An inputter that returns training examples (parallel features and labels)."""

    def __init__(
        self,
        features_inputter,
        labels_inputter,
        share_parameters=False,
        accepted_annotations=None,
    ):
        """Initializes this inputter.

        Args:
          features_inputter: An inputter producing the features (source).
          labels_inputter: An inputter producing the labels (target).
          share_parameters: Share the inputters parameters.
          accepted_annotations: An optional dictionary mapping annotation names in
            the data configuration (e.g. "train_alignments") to a callable with
            signature ``(features, labels, annotations) -> (features, labels)``.
        """
        self.features_inputter = features_inputter
        self.labels_inputter = labels_inputter
        super().__init__(
            [self.features_inputter, self.labels_inputter],
            share_parameters=share_parameters,
            combine_features=False,
        )
        # Set a meaningful prefix for source and target.
        self.features_inputter.asset_prefix = "source_"
        self.labels_inputter.asset_prefix = "target_"

        self.accepted_annotations = accepted_annotations or {}
        self.accepted_annotations["example_weights"] = _register_example_weight
        self.annotation_files = {}

    def initialize(self, data_config):
        super().initialize(data_config)

        # Check if some accepted annotations are defined in the data configuration.
        for annotation in self.accepted_annotations.keys():
            path = data_config.get(annotation)
            if path is not None:
                self.annotation_files[annotation] = path

    def make_dataset(self, data_file, training=None):
        dataset = super().make_dataset(data_file, training=training)
        if not training or not self.annotation_files:
            return dataset

        # Some annotations are configured and should be zipped to the training dataset.
        all_annotation_datasets = tf.nest.map_structure(
            tf.data.TextLineDataset, self.annotation_files
        )

        # Common case of a non-weighted dataset.
        if not isinstance(dataset, list):
            return tf.data.Dataset.zip({"examples": dataset, **all_annotation_datasets})

        # Otherwise, there should be as many annotations datasets as input datasets.
        datasets = dataset
        for name, annotation_datasets in all_annotation_datasets.items():
            num_annotation_datasets = (
                len(annotation_datasets) if isinstance(annotation_datasets, list) else 1
            )
            if num_annotation_datasets != len(datasets):
                raise ValueError(
                    "%d '%s' files were provided, but %d were expected to match the "
                    "number of data files"
                    % (num_annotation_datasets, name, len(datasets))
                )

        # Convert dict of lists to list of dicts.
        all_annotation_datasets = [
            dict(zip(all_annotation_datasets, t))
            for t in zip(*all_annotation_datasets.values())
        ]

        return [
            tf.data.Dataset.zip({"examples": dataset, **annotation_datasets})
            for dataset, annotation_datasets in zip(datasets, all_annotation_datasets)
        ]

    def get_dataset_size(self, data_file):
        size = super().get_dataset_size(data_file)
        if size is not None:
            for annotation, path in self.annotation_files.items():
                annotation_size = tf.nest.map_structure(misc.count_lines, path)
                if size != annotation_size:
                    raise RuntimeError(
                        "Annotation dataset '%s' does not have the same size as "
                        "the examples dataset" % annotation
                    )
        return size

    def make_features(self, element=None, features=None, training=None):
        if training and self.annotation_files:
            annotations = element.copy()
            example = annotations.pop("examples")
        else:
            annotations = {}
            example = element

        features, labels = super().make_features(
            element=example, features=features, training=training
        )

        # Load each annotation into the features and labels dict.
        for name, annotation in annotations.items():
            features, labels = self.accepted_annotations[name](
                features, labels, annotation
            )
        return features, labels

    def make_inference_dataset(
        self,
        features_file,
        batch_size,
        batch_type="examples",
        length_bucket_width=None,
        num_threads=1,
        prefetch_buffer_size=None,
    ):
        return self.features_inputter.make_inference_dataset(
            features_file,
            batch_size,
            batch_type=batch_type,
            length_bucket_width=length_bucket_width,
            num_threads=num_threads,
            prefetch_buffer_size=prefetch_buffer_size,
        )


def _get_dataset_transforms(
    inputter,
    num_threads=None,
    training=None,
    prepare_batch_size=128,
):
    transform_fns = []

    if inputter.has_prepare_step():
        prepare_fn = lambda *arg: inputter.prepare_elements(
            misc.item_or_tuple(arg), training=training
        )
        transform_fns.extend(
            [
                lambda dataset: dataset.batch(prepare_batch_size),
                lambda dataset: dataset.map(prepare_fn, num_parallel_calls=num_threads),
                lambda dataset: dataset.unbatch(),
            ]
        )

    map_fn = lambda *arg: inputter.make_features(
        element=misc.item_or_tuple(arg), training=training
    )
    transform_fns.append(
        lambda dataset: dataset.map(map_fn, num_parallel_calls=num_threads)
    )
    return transform_fns
