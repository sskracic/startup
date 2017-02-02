# Copyright 2017 Sebastian Skracic

import logging
import os
import MySQLdb
from flask import Flask, request
import requests
import requests_toolbelt.adapters.appengine
import re
from base64 import b64encode, b64decode
import rsa
import hashlib
import math
from bs4 import BeautifulSoup
import jinja2

JINJA_ENVIRONMENT = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True)

# Use the App Engine Requests adapter. This makes sure that Requests uses
# URLFetch.
requests_toolbelt.adapters.appengine.monkeypatch()
# [END imports]

app = Flask(__name__)
app.debug = True

# These environment variables are configured in app.yaml.
CLOUDSQL_CONNECTION_NAME = os.environ.get('CLOUDSQL_CONNECTION_NAME')
CLOUDSQL_USER = os.environ.get('CLOUDSQL_USER')
CLOUDSQL_PASSWORD = os.environ.get('CLOUDSQL_PASSWORD')
CLOUDSQL_DBNAME = os.environ.get('CLOUDSQL_DBNAME')

WORD_REGEX = re.compile('(?![0-9])[a-z]+', re.IGNORECASE)
MAX_WORDS = 100
WORD_HASH_SALT = 'NaCl'

MIN_FONT_SIZE = 10
FONT_SIZE_FACTOR = 50.0

PUB_KEY = rsa.PublicKey.load_pkcs1(os.environ.get('OCTOPUS_PUBLIC_KEY'))
PRIV_KEY = rsa.PrivateKey.load_pkcs1(os.environ.get('OCTOPUS_PRIVATE_KEY'))

def connect_to_cloudsql():
    if os.getenv('SERVER_SOFTWARE', '').startswith('Google App Engine/'):
        cloudsql_unix_socket = os.path.join(
            '/cloudsql', CLOUDSQL_CONNECTION_NAME)
        db = MySQLdb.connect(
            unix_socket=cloudsql_unix_socket,
            user=CLOUDSQL_USER,
            passwd=CLOUDSQL_PASSWORD,
            db=CLOUDSQL_DBNAME)
    else:
        db = MySQLdb.connect(
            host='127.0.0.1', user=CLOUDSQL_USER, passwd=CLOUDSQL_PASSWORD, db=CLOUDSQL_DBNAME)
    return db

def build_word_frequencies(text):
    frequencies = {}
    # First isolate the individual words
    for match in WORD_REGEX.finditer(text):
        word = match.group().lower();
        frequencies[word] = frequencies.get(word, 0) + 1
    # We're only interested in top MAX_WORDS most frequent words
    # We're returning sorted list of (word, count) tuples
    return sorted(frequencies.items(), key=lambda x : x[1], reverse=True)[:MAX_WORDS]


def update_frequency_db(word_freqs):
    db = connect_to_cloudsql()
    cursor = db.cursor()
    # We are updating only MAX_WORDS of the most frequent words
    # Sort by number of occurrences (x[1]), descending
    for word, count in word_freqs:
        # First an optional INSERT
        word_hash = hash(word)
        word_crypted = encrypt(word)

        # First an optional INSERT
        cursor.execute("""INSERT INTO word_digests (hash, word, word_count)
                                SELECT %s, %s, 0 FROM DUAL
                                WHERE NOT EXISTS
                                    (SELECT 1 FROM word_digests x
                                     WHERE x.hash = %s)""", (word_hash, word_crypted, word_hash))
        # ... followed by a mandatory UPDATE
        cursor.execute("""UPDATE word_digests
                              SET word_count = word_count + %s
                              WHERE hash = %s""", (count, word_hash))
    db.commit()

def hash(word):
    h = hashlib.sha256()
    h.update(WORD_HASH_SALT)
    h.update(word)
    return h.hexdigest()

def encrypt(msg):
    payload = rsa.encrypt(msg.encode('utf8'), PUB_KEY)
    ret = b64encode(payload)
    return ret

def decrypt(stuff):
    return rsa.decrypt(b64decode(stuff), PRIV_KEY).decode('utf8')

def get_raw_text_from_url(url):
    response = requests.get(url)
    response.raise_for_status()
    text = response.text
    raw = BeautifulSoup(text, 'html.parser').get_text(' ')
    return raw

def font_size(n):
    return int(MIN_FONT_SIZE + FONT_SIZE_FACTOR * math.sqrt(n))

@app.route('/')
def url_form():
    template = JINJA_ENVIRONMENT.get_template('index.html')
    return template.render({})

@app.route('/tag-cloud', methods=['POST'])
def tag_cloud():
    url = request.form['url']
    logging.info("Requesting URL: {}".format(url))
    text = get_raw_text_from_url(request.form['url'])
    word_freqs = build_word_frequencies(text)
    update_frequency_db(word_freqs)
    normalized_word_freqs = [(t[0], t[1], font_size(t[1])) for t in word_freqs]
    template_values = {
        'url':   url,
        'tags':  normalized_word_freqs
    }
    template = JINJA_ENVIRONMENT.get_template('tag-cloud.html')
    return template.render(template_values)

@app.route('/clear')
def clear_index():
    db = connect_to_cloudsql()
    cursor = db.cursor()
    cursor.execute("DELETE FROM word_digests")
    db.commit()
    return words_in_index()

@app.route('/words')
def words_in_index():
    db = connect_to_cloudsql()
    cursor = db.cursor()
    cursor.execute("SELECT word, word_count FROM word_digests ORDER BY word_count DESC")
    decrypted_words = [(decrypt(t[0]), t[1]) for t in cursor.fetchall()]
    template_values = {
        'words':  decrypted_words
    }
    template = JINJA_ENVIRONMENT.get_template('words-in-index.html')
    return template.render(template_values)


@app.errorhandler(Exception)
def server_error(e):
    logging.exception('An error occurred during a request.')
    return """
    An internal error occurred: <pre>{}</pre>
    See logs for full stacktrace.
    """.format(e), 500

