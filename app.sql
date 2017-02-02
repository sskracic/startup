
CREATE TABLE word_digests (
    hash         VARCHAR(64) NOT NULL,
    word         TEXT NOT NULL,
    word_count   INTEGER NOT NULL,
    CONSTRAINT PRIMARY KEY word_digests_pk(hash)
);

