import gzip
import io
import os

import tensorflow as tf
import yaml

from google.protobuf import text_format
from parameterized import parameterized
from tensorboard.plugins import projector

from yimt.core import inputters
from yimt.core.data import dataset as dataset_util
from yimt.core.data import noise
from yimt.core.inputters import inputter, text_inputter
from yimt.core.tests import test_util
from yimt.core.utils.misc import count_lines, item_or_tuple


class InputterTest(tf.test.TestCase):

    def _makeTextFile(self, name, lines, compress=False):
        path = os.path.join(self.get_temp_dir(), name)
        if compress:
            path = "%s.gz" % path
        with (gzip if compress else io).open(path, mode="wt", encoding="utf-8") as f:
            for line in lines:
                f.write("%s\n" % line)
        return path

    def _makeEmbeddingsFile(self, vectors, name="embedding", header=False):
        path = os.path.join(self.get_temp_dir(), name)
        with open(path, "w") as embs:
            if header:
                embs.write("%d %d\n" % (len(vectors), len(vectors[0][1])))
            for word, vector in vectors:
                embs.write("%s %s\n" % (word, " ".join(str(v) for v in vector)))
        return path

    def testPretrainedEmbeddingsLoading(self):
        vocab_file = self._makeTextFile("vocab.txt", ["Toto", "tOTO", "tata", "tete"])
        embedding_file = self._makeEmbeddingsFile(
            [("toto", [1, 1]), ("titi", [2, 2]), ("tata", [3, 3])]
        )

        embeddings = text_inputter.load_pretrained_embeddings(
            embedding_file,
            vocab_file,
            num_oov_buckets=1,
            with_header=False,
            case_insensitive_embeddings=True,
        )
        self.assertAllEqual([5, 2], embeddings.shape)
        self.assertAllEqual([1, 1], embeddings[0])
        self.assertAllEqual([1, 1], embeddings[1])
        self.assertAllEqual([3, 3], embeddings[2])

        embeddings = text_inputter.load_pretrained_embeddings(
            embedding_file,
            vocab_file,
            num_oov_buckets=2,
            with_header=False,
            case_insensitive_embeddings=False,
        )
        self.assertAllEqual([6, 2], embeddings.shape)
        self.assertAllEqual([3, 3], embeddings[2])

    def testPretrainedEmbeddingsWithHeaderLoading(self):
        vocab_file = self._makeTextFile("vocab.txt", ["Toto", "tOTO", "tata", "tete"])
        embedding_file = self._makeEmbeddingsFile(
            [("toto", [1, 1]), ("titi", [2, 2]), ("tata", [3, 3])], header=True
        )

        embeddings = text_inputter.load_pretrained_embeddings(
            embedding_file,
            vocab_file,
            num_oov_buckets=1,
            case_insensitive_embeddings=True,
        )
        self.assertAllEqual([5, 2], embeddings.shape)
        self.assertAllEqual([1, 1], embeddings[0])
        self.assertAllEqual([1, 1], embeddings[1])
        self.assertAllEqual([3, 3], embeddings[2])

    @parameterized.expand(
        [
            [[3, 4], 2, 1, 2, [1, 3, 4, 2], 4],
            [[[3, 4], [5, 6]], [2, 1], 1, None, [[1, 3, 4], [1, 5, 0]], [3, 2]],
            [[[3, 4], [5, 6]], [2, 1], None, 2, [[3, 4, 2], [5, 2, 0]], [3, 2]],
        ]
    )
    def testAddSequenceControls(
        self, ids, length, start_id, end_id, expected_ids, expected_length
    ):
        ids = tf.constant(ids, dtype=tf.int64)
        length = tf.constant(length, dtype=tf.int32)
        ids, length = inputters.add_sequence_controls(
            ids, length, start_id=start_id, end_id=end_id
        )
        self.assertAllEqual(self.evaluate(ids), expected_ids)
        self.assertAllEqual(self.evaluate(length), expected_length)

    def testAddSequenceControlsRagged(self):
        ids = tf.RaggedTensor.from_tensor([[4, 5, 6], [3, 4, 0]], padding=0)
        ids = inputters.add_sequence_controls(ids, start_id=1, end_id=2)
        self.assertAllEqual(ids.to_list(), [[1, 4, 5, 6, 2], [1, 3, 4, 2]])

    def _checkFeatures(self, features, expected_shapes):
        for name, expected_shape in expected_shapes.items():
            self.assertIn(name, features)
            self.assertTrue(features[name].shape.is_compatible_with(expected_shape))

    def _testServing(self, inputter):
        @tf.function(input_signature=(inputter.input_signature(),))
        def _serving_fun(features):
            features = inputter.make_features(features=features.copy())
            inputs = inputter(features)
            return inputs

        _serving_fun.get_concrete_function()

    def _makeDataset(
        self, inputter, data_file, data_config=None, dataset_size=1, shapes=None
    ):
        if data_config is not None:
            inputter.initialize(data_config)
        self.assertEqual(inputter.get_dataset_size(data_file), dataset_size)
        dataset = inputter.make_dataset(data_file)
        eager_features = inputter.make_features(iter(dataset).next(), training=True)
        eager_features = tf.nest.map_structure(
            lambda t: tf.expand_dims(t, 0), eager_features
        )
        dataset = dataset.map(
            lambda *arg: inputter.make_features(item_or_tuple(arg), training=True)
        )
        dataset = dataset.apply(dataset_util.batch_dataset(1))
        features = iter(dataset).next()
        if shapes is not None:
            self._checkFeatures(features, shapes)
            self._checkFeatures(eager_features, shapes)
        keep = inputter.keep_for_training(features)
        self.assertIs(keep.dtype, tf.bool)
        inputs = inputter(features, training=True)
        if not isinstance(inputter, inputters.ExampleInputter):
            self._testServing(inputter)
        return self.evaluate((features, inputs))

    def testWordEmbedder(self):
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello", "toto"])
        data_file = self._makeTextFile("data.txt", ["hello world !"])

        embedder = text_inputter.WordEmbedder(embedding_size=10)
        features, transformed = self._makeDataset(
            embedder,
            data_file,
            data_config={"vocabulary": vocab_file},
            shapes={"tokens": [None, None], "ids": [None, None], "length": [None]},
        )

        self.assertAllEqual([3], features["length"])
        self.assertAllEqual([[2, 1, 4]], features["ids"])
        self.assertAllEqual([1, 3, 10], transformed.shape)

        oov_tokens = embedder.get_oov_tokens(features)
        self.assertListEqual(oov_tokens.numpy().flatten().tolist(), [b"!"])

    def testWordEmbedderForDecoder(self):
        vocab_file = test_util.make_vocab(
            os.path.join(self.get_temp_dir(), "vocab.txt"),
            ["the", "world", "hello", "toto"],
        )
        embedder = text_inputter.WordEmbedder(embedding_size=10)
        embedder.set_decoder_mode(mark_start=True, mark_end=True)
        embedder.initialize({"vocabulary": vocab_file})
        features = embedder.make_features(tf.constant("hello world !"))
        self.assertEqual(features["length"], 4)
        self.assertEqual(embedder.get_length(features, ignore_special_tokens=True), 3)
        self.assertAllEqual(features["ids"], [1, 5, 4, 7])
        self.assertAllEqual(features["ids_out"], [5, 4, 7, 2])

        oov_tokens = embedder.get_oov_tokens(features)
        self.assertListEqual(oov_tokens.numpy().flatten().tolist(), [b"!"])

    def testWordEmbedderWithInGraphTokenizer(self):
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello", "￭"])
        embedder = text_inputter.WordEmbedder(embedding_size=10)
        data_config = {
            "vocabulary": vocab_file,
            "tokenization": {"type": "CharacterTokenizer"},
        }
        embedder.initialize(data_config)
        self.assertIn("text", embedder.input_signature())
        self._testServing(embedder)

    def testWordEmbedderWithCompression(self):
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello", "￭"])
        data_file = self._makeTextFile(
            "data.txt", ["hello world !", "how are you ?"], compress=True
        )
        inputter = text_inputter.WordEmbedder(embedding_size=10)
        inputter.initialize(dict(vocabulary=vocab_file))
        dataset = inputter.make_inference_dataset(data_file, batch_size=1)
        iterator = iter(dataset)
        self.assertAllEqual(
            next(iterator)["tokens"].numpy()[0], [b"hello", b"world", b"!"]
        )

    def testWordEmbedderWithNoise(self):
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello"])
        data_file = self._makeTextFile("data.txt", ["hello world !"])
        noiser = noise.WordNoiser(noises=[noise.WordOmission(1)])
        embedder = text_inputter.WordEmbedder(embedding_size=10)
        embedder.set_noise(noiser, in_place=False)
        expected_shapes = {
            "tokens": [None, None],
            "ids": [None, None],
            "length": [None],
            "noisy_tokens": [None, None],
            "noisy_ids": [None, None],
            "noisy_length": [None],
        }
        features, transformed = self._makeDataset(
            embedder,
            data_file,
            data_config={"vocabulary": vocab_file},
            shapes=expected_shapes,
        )
        self.assertEqual(features["noisy_length"][0], features["length"][0] - 1)

    @parameterized.expand([[1], [0]])
    def testWordEmbedderWithInPlaceNoise(self, probability):
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello"])
        data_file = self._makeTextFile("data.txt", ["hello world !"])
        noiser = noise.WordNoiser(noises=[noise.WordOmission(1)])
        embedder = text_inputter.WordEmbedder(embedding_size=10)
        embedder.set_noise(noiser, probability=probability)
        features, transformed = self._makeDataset(
            embedder,
            data_file,
            data_config={"vocabulary": vocab_file},
            shapes={"tokens": [None, None], "ids": [None, None], "length": [None]},
        )
        self.assertEqual(features["length"][0], 3 if probability == 0 else 2)

    def testWordEmbedderWithPretrainedEmbeddings(self):
        data_file = self._makeTextFile("data.txt", ["hello world !"])
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello", "toto"])
        embedding_file = self._makeEmbeddingsFile(
            [("hello", [1, 1]), ("world", [2, 2]), ("toto", [3, 3])]
        )

        embedder = text_inputter.WordEmbedder()
        data = {
            "vocabulary": vocab_file,
            "embedding": {"path": embedding_file, "with_header": False},
        }
        features, transformed = self._makeDataset(
            embedder,
            data_file,
            data_config=data,
            shapes={"tokens": [None, None], "ids": [None, None], "length": [None]},
        )

        self.assertAllEqual([1, 1], transformed[0][0])
        self.assertAllEqual([2, 2], transformed[0][1])

    def testWordEmbedderMissingInitialization(self):
        embedder = text_inputter.WordEmbedder()
        with self.assertRaisesRegex(RuntimeError, "initialize"):
            embedder.input_signature()
        with self.assertRaisesRegex(RuntimeError, "initialize"):
            embedder.make_features("Hello world !")

    def testWordEmbedderBatchElement(self):
        vocab_file = self._makeTextFile(
            "vocab.txt", ["<blank>", "<s>", "</s>"] + list(map(str, range(10)))
        )
        embedder = text_inputter.WordEmbedder(32)
        embedder.initialize(dict(vocabulary=vocab_file))
        features = embedder.make_features(["1 2 3", "1 2 3 4"])
        self.assertAllEqual(features["length"], [3, 4])
        self.assertAllEqual(features["ids"], [[4, 5, 6, 0], [4, 5, 6, 7]])

        embedder.set_decoder_mode(mark_start=True, mark_end=True)
        features = embedder.make_features(["1 2 3", "1 2 3 4"])
        self.assertAllEqual(features["length"], [4, 5])
        self.assertAllEqual(features["ids"], [[1, 4, 5, 6, 0], [1, 4, 5, 6, 7]])
        self.assertAllEqual(features["ids_out"], [[4, 5, 6, 2, 0], [4, 5, 6, 7, 2]])

    def testParallelInputter(self):
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello", "toto"])
        data_file = self._makeTextFile("data.txt", ["hello world !"])

        data_files = [data_file, data_file]

        parallel_inputter = inputter.ParallelInputter(
            [
                text_inputter.WordEmbedder(embedding_size=10),
                text_inputter.WordEmbedder(embedding_size=5),
            ]
        )
        self.assertEqual(parallel_inputter.num_outputs, 2)
        features, transformed = self._makeDataset(
            parallel_inputter,
            data_files,
            data_config={"1_vocabulary": vocab_file, "2_vocabulary": vocab_file},
            shapes={
                "inputter_0_ids": [None, None],
                "inputter_0_length": [None],
                "inputter_1_ids": [None, None],
                "inputter_1_length": [None],
            },
        )

        self.assertEqual(2, len(parallel_inputter.get_length(features)))
        self.assertEqual(2, len(transformed))
        self.assertAllEqual([1, 3, 10], transformed[0].shape)
        self.assertAllEqual([1, 3, 5], transformed[1].shape)

    def testParallelInputterShareParameters(self):
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello", "toto"])
        data_config = {"1_vocabulary": vocab_file, "2_vocabulary": vocab_file}
        inputters = [
            text_inputter.WordEmbedder(embedding_size=10),
            text_inputter.WordEmbedder(embedding_size=10),
        ]
        parallel_inputter = inputter.ParallelInputter(inputters, share_parameters=True)
        parallel_inputter.initialize(data_config)
        parallel_inputter.build(None)
        self.assertEqual(inputters[0].embedding.ref(), inputters[1].embedding.ref())

    def testNestedParallelInputterShareParameters(self):
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello", "toto"])
        data_config = {
            "1_1_vocabulary": vocab_file,
            "1_2_vocabulary": vocab_file,
            "2_vocabulary": vocab_file,
        }
        source_inputters = [
            text_inputter.WordEmbedder(embedding_size=10),
            text_inputter.WordEmbedder(embedding_size=10),
        ]
        target_inputter = text_inputter.WordEmbedder(embedding_size=10)
        inputters = [
            inputter.ParallelInputter(source_inputters, share_parameters=True),
            target_inputter,
        ]
        parallel_inputter = inputter.ParallelInputter(inputters, share_parameters=True)
        parallel_inputter.initialize(data_config)
        parallel_inputter.build(None)
        self.assertEqual(
            source_inputters[0].embedding.ref(), target_inputter.embedding.ref()
        )
        self.assertEqual(
            source_inputters[1].embedding.ref(), target_inputter.embedding.ref()
        )

    def testExampleInputter(self):
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello", "toto"])
        data_file = self._makeTextFile("data.txt", ["hello world !"])

        source_inputter = text_inputter.WordEmbedder(embedding_size=10)
        target_inputter = text_inputter.WordEmbedder(embedding_size=10)
        example_inputter = inputter.ExampleInputter(source_inputter, target_inputter)
        self.assertEqual(example_inputter.num_outputs, 2)

        features, transformed = self._makeDataset(
            example_inputter,
            [data_file, data_file],
            data_config={
                "source_vocabulary": vocab_file,
                "target_vocabulary": vocab_file,
            },
        )

        self.assertIsInstance(features, tuple)
        self.assertEqual(len(features), 2)
        self.assertEqual(len(transformed), 2)
        features, labels = features
        for field in ("ids", "length", "tokens"):
            self.assertIn(field, features)
        for field in ("ids", "length", "tokens"):
            self.assertIn(field, labels)

    def testExampleInputterFiltering(self):
        vocab_file = self._makeTextFile("vocab.txt", ["a", "b", "c", "d"])
        features_file = self._makeTextFile(
            "features.txt", ["a b c d", "a b c", "a a c", "a"]
        )
        labels_file = self._makeTextFile(
            "labels.txt", ["a b c d", "a", "a a c d d", ""]
        )

        example_inputter = inputter.ExampleInputter(
            text_inputter.WordEmbedder(embedding_size=10),
            text_inputter.WordEmbedder(embedding_size=10),
        )
        example_inputter.initialize(
            {"source_vocabulary": vocab_file, "target_vocabulary": vocab_file}
        )

        dataset = example_inputter.make_training_dataset(
            features_file,
            labels_file,
            batch_size=1,
            maximum_features_length=3,
            maximum_labels_length=4,
            single_pass=True,
        )
        examples = list(iter(dataset))
        self.assertLen(examples, 1)
        self.assertAllEqual(examples[0][0]["ids"], [[0, 1, 2]])
        self.assertAllEqual(examples[0][1]["ids"], [[0]])

    def testExampleInputterMismatchedDatasetSize(self):
        vocab_file = self._makeTextFile("vocab.txt", ["a", "b", "c", "d"])
        features_file = self._makeTextFile("features.txt", ["a", "b", "c", "d"])
        labels_file = self._makeTextFile("labels.txt", ["a", "b", "c"])

        example_inputter = inputter.ExampleInputter(
            text_inputter.WordEmbedder(embedding_size=10),
            text_inputter.WordEmbedder(embedding_size=10),
        )
        example_inputter.initialize(
            {"source_vocabulary": vocab_file, "target_vocabulary": vocab_file}
        )

        with self.assertRaisesRegex(RuntimeError, "same size"):
            example_inputter.make_training_dataset(
                features_file,
                labels_file,
                batch_size=2,
            )

    def testExampleInputterMismatchedAnnotationsSize(self):
        vocab_file = self._makeTextFile("vocab.txt", ["a", "b", "c", "d"])
        features_file = self._makeTextFile("features.txt", ["a", "b", "c"])
        labels_file = self._makeTextFile("labels.txt", ["a", "b", "c"])
        weights_file = self._makeTextFile("weights.txt", ["1.0", "1.0"])

        example_inputter = inputter.ExampleInputter(
            text_inputter.WordEmbedder(embedding_size=10),
            text_inputter.WordEmbedder(embedding_size=10),
        )
        example_inputter.initialize(
            {
                "source_vocabulary": vocab_file,
                "target_vocabulary": vocab_file,
                "example_weights": weights_file,
            }
        )

        with self.assertRaisesRegex(RuntimeError, "same size"):
            example_inputter.make_training_dataset(
                features_file,
                labels_file,
                batch_size=2,
            )

    def testWeightedDataset(self):
        vocab_file = self._makeTextFile("vocab.txt", ["the", "world", "hello", "toto"])
        data_file = self._makeTextFile("data.txt", ["hello world !"])
        source_inputter = text_inputter.WordEmbedder(embedding_size=10)
        target_inputter = text_inputter.WordEmbedder(embedding_size=10)
        example_inputter = inputter.ExampleInputter(source_inputter, target_inputter)
        example_inputter.initialize(
            {"source_vocabulary": vocab_file, "target_vocabulary": vocab_file}
        )
        with self.assertRaisesRegex(ValueError, "same number"):
            example_inputter.make_training_dataset(
                [data_file, data_file], [data_file], batch_size=16
            )
        with self.assertRaisesRegex(ValueError, "expected to match"):
            example_inputter.make_training_dataset(
                [data_file, data_file],
                [data_file, data_file],
                batch_size=16,
                weights=[0.5],
            )
        dataset = example_inputter.make_training_dataset(
            [data_file, data_file], [data_file, data_file], batch_size=16
        )
        self.assertIsInstance(dataset, tf.data.Dataset)
        dataset = example_inputter.make_training_dataset(
            [data_file, data_file],
            [data_file, data_file],
            batch_size=16,
            weights=[0.2, 0.8],
        )
        self.assertIsInstance(dataset, tf.data.Dataset)

    def testBatchAutotuneDataset(self):
        vocab_file = self._makeTextFile("vocab.txt", ["1", "2", "3", "4"])
        data_file = self._makeTextFile("data.txt", ["hello world !"])
        source_inputter = text_inputter.WordEmbedder(embedding_size=10)
        target_inputter = text_inputter.WordEmbedder(embedding_size=10)
        target_inputter.set_decoder_mode(mark_start=True, mark_end=True)
        example_inputter = inputter.ExampleInputter(source_inputter, target_inputter)
        example_inputter.initialize(
            {"source_vocabulary": vocab_file, "target_vocabulary": vocab_file}
        )

        dataset = example_inputter.make_training_dataset(
            data_file,
            data_file,
            batch_size=1024,
            batch_type="tokens",
            maximum_features_length=100,
            maximum_labels_length=120,
            batch_autotune_mode=True,
        )

        source_spec, target_spec = dataset.element_spec
        self.assertListEqual(source_spec["ids"].shape.as_list(), [None, None])

        source, target = next(iter(dataset))
        self.assertListEqual(source["ids"].shape.as_list(), [8, 100])
        self.assertListEqual(target["ids"].shape.as_list(), [8, 120])
        self.assertListEqual(target["ids_out"].shape.as_list(), [8, 120])

    def testBatchAutotuneDatasetMultiSource(self):
        vocab_file = self._makeTextFile("vocab.txt", ["1", "2", "3", "4"])
        data_file = self._makeTextFile("data.txt", ["hello world !"])
        source_inputter = inputter.ParallelInputter(
            [
                text_inputter.WordEmbedder(embedding_size=10),
                text_inputter.WordEmbedder(embedding_size=10),
            ]
        )
        target_inputter = text_inputter.WordEmbedder(embedding_size=10)
        target_inputter.set_decoder_mode(mark_start=True, mark_end=True)
        example_inputter = inputter.ExampleInputter(source_inputter, target_inputter)
        example_inputter.initialize(
            {
                "source_1_vocabulary": vocab_file,
                "source_2_vocabulary": vocab_file,
                "target_vocabulary": vocab_file,
            }
        )

        dataset = example_inputter.make_training_dataset(
            [data_file, data_file],
            data_file,
            batch_size=1024,
            batch_type="tokens",
            maximum_features_length=[100, 110],
            maximum_labels_length=120,
            batch_autotune_mode=True,
        )

        source, target = next(iter(dataset))
        self.assertListEqual(source["inputter_0_ids"].shape.as_list(), [8, 100])
        self.assertListEqual(source["inputter_1_ids"].shape.as_list(), [8, 110])
        self.assertListEqual(target["ids"].shape.as_list(), [8, 120])
        self.assertListEqual(target["ids_out"].shape.as_list(), [8, 120])


if __name__ == "__main__":
    tf.test.main()
