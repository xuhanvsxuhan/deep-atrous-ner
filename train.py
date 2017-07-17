from data.conll_loader import ConllLoader
from model.model import *
from model.trainer import classifier_train

__author__ = 'georgi.val.stoyan0v@gmail.com'

BATCH_SIZE = 256

BUCKETS = [20, 60, 80, 120, 180]
DATA_FILE = ['./data/datasets/conll_2003/eng.train']
TEST_FILES = ['./data/datasets/conll_2003/eng.testa']

data = ConllLoader(BUCKETS, DATA_FILE, batch_size=BATCH_SIZE)
validation = ConllLoader(BUCKETS, TEST_FILES, batch_size=BATCH_SIZE, table=data.table, table_pos=data.table_pos,
                         table_chunk=data.table_chunk, table_entity=data.table_entity)

# session with multiple GPU support
sess = tf.Session(config=tf.ConfigProto(allow_soft_placement=True))

# setup embeddings, preload pre-trained embeddings if needed
word_emb = None
pos_emb = None
chunk_emb = None
entities_emb = None
word_embedding_name = 'word_emb'

if use_pre_trained_embeddings:
    embedding_matrix = data.preload_embeddings(embedding_dim, pre_trained_embeddings_file)
    word_emb = init_custom_embeddings(name=word_embedding_name, embeddings_matrix=embedding_matrix, trainable=False)
else:
    word_emb = tf.sg_emb(name=word_embedding_name, voca_size=data.vocabulary_size, dim=embedding_dim)

z_w = data.source_words.sg_lookup(emb=word_emb)
z_p = tf.one_hot(data.source_pos, depth=num_pos)
z_c = tf.one_hot(data.source_chunk, depth=num_chunk)
z_cap = data.source_capitals.sg_cast(dtype=tf.float32)

# we concatenated all inputs into one single input vector
z_i = tf.split(tf.concat([z_w, z_p, z_c, z_cap], 2), tf.sg_gpus())

v_w = validation.source_words.sg_lookup(emb=word_emb)
v_p = tf.one_hot(validation.source_pos, depth=num_pos)
v_c = tf.one_hot(validation.source_chunk, depth=num_chunk)
v_cap = validation.source_capitals.sg_cast(dtype=tf.float32)

# we concatenated all inputs into one single input vector
v_i = tf.split(tf.concat([v_w, v_p, v_c, v_cap], 2), tf.sg_gpus())

entities = tf.split(data.entities, tf.sg_gpus())
val_entities = tf.split(validation.entities, tf.sg_gpus())


# setup the model for training and validation. Enable multi-GPU support
@tf.sg_parallel
def get_train_loss(opt):
    with tf.sg_context(name='model'):
        labels = opt.entities[opt.gpu_index]

        # train_classifier = rnn_model(opt.z_i[opt.gpu_index], num_labels)
        train_classifier = decode(opt.z_i[opt.gpu_index], num_labels)

        # cross entropy loss with logit
        loss = train_classifier.ner_cost(target=labels, num_classes=num_labels)

        return loss


@tf.sg_parallel
def get_val_metrics(opt):
    with tf.sg_context(name='model', reuse=True):
        tf.get_variable_scope().reuse_variables()

        val_labels = opt.entities[opt.gpu_index]

        # test_classifier = rnn_model(opt.v_i[opt.gpu_index], num_labels, is_test=True)
        test_classifier = decode(opt.v_i[opt.gpu_index], num_labels, test=True)
        val_predictions = test_classifier.sg_argmax() + 1

        # accuracy evaluation (validation set)
        val_acc = test_classifier.ner_accuracy(target=val_labels, mask=True, name='accuracy')

        # validation loss
        val_loss = test_classifier.ner_cost(target=val_labels, num_classes=num_labels, name='val_loss')

        return val_acc, val_loss, val_predictions, val_labels


tf.sg_init(sess)
data.visualize_embeddings(sess, word_emb, word_embedding_name)

# train
classifier_train(sess=sess, log_interval=30, lr=3e-3, clip_grad_norm=10, optim='Adam', max_keep=10,
                 loss=get_train_loss(z_i=z_i, entities=entities)[0],
                 eval_metric=get_val_metrics(v_i=v_i, entities=val_entities)[0], ep_size=data.num_batches,
                 val_ep_size=validation.num_batches, max_ep=150, early_stop=False)
