"""
Download and store pretrained models that can be used to create embedding
layers.

These models take pretrained files, downloaded over HTTP, and compile them
into dense tensor representation using a memory mapping back end.

>>> from vectoria import Embeddings
>>> chargram = Embeddings.CharacterTrigramFastText(language='en')
>>> chargram.embeddings.shape
(2519370, 300)
>>> chargram.embed('hello')[0:4, 0:10]
array([[-0.33853999, -0.15685   , -0.31086001,  0.41556999, -0.32370999,
        -0.027012  ,  0.42827001, -0.15748   , -0.034061  ,  0.36767   ],
       [-0.26877001, -0.32778999, -0.47106001,  0.55541003,  0.61224002,
        -0.065267  , -0.41846001,  0.033309  ,  0.31174999,  0.56216002],
       [ 0.2438    , -0.38519999, -0.22592001, -0.17658   , -0.36068001,
         0.31208   ,  0.25608   ,  0.29251   , -0.22416   , -0.0011038 ],
       [ 0.        ,  0.        ,  0.        ,  0.        ,  0.        ,
         0.        ,  0.        ,  0.        ,  0.        ,  0.        ]], dtype=float32)
"""
import importlib
from pathlib import Path

from . import Sequencers

import numpy as np
import requests
from tqdm import tqdm
import numpy.linalg as la
import keras

FAST_TEXT_URL_TEMPLATE = "https://s3-us-west-1.amazonaws.com/fasttext-vectors/wiki.{0}.vec"

GLOVE_URL_EN = "http://nlp.stanford.edu/data/glove.6B.zip"


epsilon = np.finfo(np.float32).eps


def download_path(flavor, language):
    """
    Compute a download path for a given flavor of vectors
    and language.

    Parameters
    ----------
    flavor:
        Any string to separate different models.
    language:
        Two letter language code.

    Returns
    -------
    A `Path` object.
    """
    # the local in package file path for the language model
    pkg = importlib.import_module('vectoria')
    vectoria_path = Path(pkg.__file__).parent
    folder_path = vectoria_path / Path(language)
    if not folder_path.exists():
        folder_path.mkdir()
    vectors_path = folder_path / Path('{1}-{0}.vec'.format(flavor, language))
    return vectors_path


class CharacterTrigramFastText:
    """
    Language model base that will download and compile pretrained FastText vectors
    for a given language.

    Attributes
    ----------
    embeddings
        A two dimensional numpy array [term id, vector dimension] storing floating points.
        This is a memory mapped array to save some I/O.
    """

    def __init__(self, language='en', maxlen=1024):
        """
        Construct a language model for a given string by:
        - opening an existing model if present
        - downloading and compiling pretrained word models otherwise

        This is a mulit-gigabyte download at least for english and will take
        a while.

        Parameters
        ----------
        language:
            Two letter language code.
        maxlen: 
            Limit to this number of token parsed per document.
        """
        vectors_path = download_path('fasttext', language)
        final_path = vectors_path.with_suffix('.numpy')
        self.maxlen = maxlen
        self.sequencer = sequencer = Sequencers.CharacterTrigramSequencer(maxlen=maxlen)
        # download if needed
        if not vectors_path.exists():
            url = FAST_TEXT_URL_TEMPLATE.format(language)
            # Streaming, so we can iterate over the response.
            r = requests.get(url, stream=True)
            # Total size in bytes.
            total_size = int(r.headers.get('content-length', 0))
            with open(vectors_path.with_suffix('.tmp'), 'wb') as f:
                chunk = 32 * 1024
                progress = tqdm(total=total_size, unit='B', unit_scale=True)
                for data in r.iter_content(chunk):
                    if data:
                        f.write(data)
                        progress.update(len(data))
            vectors_path.with_suffix('.tmp').rename(vectors_path)
        # compile if needed
        if not final_path.exists():
            with open(vectors_path, 'r') as f:
                first_line = f.readline()
                words, dimensions = map(int, first_line.split())
                embeddings = np.memmap(final_path.with_suffix(
                    '.tmp'), dtype='float32', mode='w+', shape=(words, dimensions))
            for line in tqdm(iterable=open(str(vectors_path)), total=words):
                # how big is this thing?
                segments = line.split()
                if len(segments) > dimensions and len(segments[0]) > 2:
                    word = sequencer.transform([segments[0]])[0][0]
                    try:
                        numbers = np.array(list(map(np.float32, segments[1:])))
                        embeddings[word] = numbers
                    except ValueError:
                        pass
            # the zero word is a pad value
            embeddings[0] = np.zeros(dimensions)
            embeddings.flush()
            del embeddings
            final_path.with_suffix('.tmp').rename(final_path)
        # and -- actually open
        with open(vectors_path, 'r') as f:
            first_line = f.readline()
            words, dimensions = map(int, first_line.split())
            self.embeddings = np.memmap(
                final_path, dtype='float32', mode='r', shape=(words, dimensions))

        self.model = keras.models.Sequential()
        self.model.add(keras.layers.Embedding(
            self.embeddings.shape[0],
            self.embeddings.shape[1], 
            mask_zero=True,
            input_length=maxlen, 
            trainable=False, 
            weights=[self.embeddings]))

    def embed(self, str):
        """
        Given a string, turn it into a sequence of chargram identifiers, and
        then embed it.

        Parameters
        ----------
        str:
            Any string.

        Returns
        -------
        A two dimensional embedding array.
        """
        input = keras.layers.Input(shape=(self.maxlen,))
        embedded = self.model(input)
        model = keras.models.Model(input=input, output=embedded)
        model.compile(optimizer='adam', loss='mse')
        return model.predict(self.sequencer.transform([str]))[0]