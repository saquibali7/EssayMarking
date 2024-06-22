# -*- coding: utf-8 -*-
"""EssayMarking.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/15kKvFxfJeoiUHvt01vFeiwtUeO2NmyfJ
"""

from google.colab import drive
drive.mount('/content/drive')

data_path = '/content/drive/MyDrive/train.csv.zip'
!unzip -q $data_path
data = '/content/train.csv'

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

dataset = pd.read_csv(data)
print(len(dataset))
dataset.head()

import re
import nltk
import spacy
import string
from nltk.tokenize import word_tokenize
from nltk.corpus import stopwords
from nltk.stem import PorterStemmer, WordNetLemmatizer
from spacy.lang.en.stop_words import STOP_WORDS
from spacy.lang.en import English

nltk.download('punkt')
nltk.download('stopwords')
nltk.download('wordnet')

stop_words = set(stopwords.words('english'))

ps = PorterStemmer()
lemmatizer = WordNetLemmatizer()
nlp = spacy.load('en_core_web_sm')

def preprocess_text(text):
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\d+', '', text)
    tokens = word_tokenize(text)
    stop_words = set(stopwords.words('english'))
    tokens = [word for word in tokens if word not in stop_words]
    tokens = [lemmatizer.lemmatize(token) for token in tokens]
    tokens = [token for token in tokens if token not in STOP_WORDS]
    return tokens

max_length = 0
max_score = dataset['score'].max()
vocabulary = []
for text in dataset['full_text'] :
  text = preprocess_text(text)
  if len(text) > max_length:
    max_length = len(text)
  vocabulary.extend(text)


vocabulary = sorted(list(set(vocabulary)))
word_to_idx = {word: idx+1 for idx, word in enumerate(vocabulary)}

print(f"lenght of vocabulary : {len(vocabulary)}")
print(f"max_length : {max_length}")
print(f"max_score : {dataset['score'].max()}")
print(f"min_score : {dataset['score'].min()}")

from torch.utils.data import Dataset, DataLoader
from transformers import AutoTokenizer

class CustomDataset(Dataset):
    def __init__(self, dataset, word_to_idx, max_length, max_score):
        self.dataset = dataset
        self.word_to_idx = word_to_idx
        self.max_length = max_length
        self.max_score = max_score
        self.texts = dataset['full_text']
        self.scores = dataset['score']

    def __len__(self):
        return len(self.dataset)

    def __getitem__(self, idx):
        text = self.texts[idx]
        score = self.scores[idx]
        temp = [0]*max_score
        temp[int(score)-1] = 1
        score = temp
        tokens = preprocess_text(text)
        idx = [self.word_to_idx[token] for token in tokens if token in self.word_to_idx]
        idx = idx + [0] * (self.max_length - len(idx))
        return torch.tensor(idx), torch.tensor(score)

train_size = int(0.8 * len(dataset))
test_size = len(dataset) - train_size

dataset = CustomDataset(dataset, word_to_idx, max_length, max_score)

train_dataset, test_dataset = torch.utils.data.random_split(dataset, [train_size, test_size])
train_loader = DataLoader(train_dataset, batch_size=8, shuffle=True)
val_loader = DataLoader(test_dataset, batch_size=8, shuffle=True)

"""## Model"""

embd_size=256
dropout = 0.2
num_head = 4
vocab_size = len(vocabulary)
num_layers = 8

device = 'cuda' if torch.cuda.is_available() else 'cpu'
device

class Head(nn.Module):
    def __init__(self,head_size):
        super().__init__()
        self.key = nn.Linear(embd_size, head_size)
        self.query = nn.Linear(embd_size, head_size)
        self.value = nn.Linear(embd_size, head_size)
        self.register_buffer('tril', torch.tril(torch.ones(max_length, max_length)))
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        B, T, C = x.shape
        query = self.query(x)
        key = self.query(x)
        value = self.value(x)
        wei = query @ key.transpose(-2, -1) ** key.shape[-1]** -1
        wei = wei.masked_fill(self.tril[:T, :T] == 0, float('-inf'))
        wei = F.softmax(wei, dim=-1)
        wei = self.dropout(wei)
        out = wei@value
        return out

class MultiHeadAttention(nn.Module):
    def __init__(self, head_size, num_heads):
        super().__init__()
        self.head = nn.ModuleList([Head(head_size) for _ in range(num_heads)])
        self.proj = nn.Linear(head_size*num_heads, embd_size)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        out = torch.cat([H(x) for H in self.head], dim=-1)
        out = self.dropout(self.proj(x))
        return x

class FeedForwad(nn.Module):
    def __init__(self, embd_size):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(embd_size, 4*embd_size),
            nn.ReLU(),
            nn.Linear(4*embd_size, embd_size),
            nn.Dropout(dropout)
        )

    def forward(self, x):
        return self.net(x)


class Block(nn.Module):
    def __init__(self, num_head, embed_size):
        super().__init__()
        head_size = embd_size // num_head
        self.sa = MultiHeadAttention(head_size, num_head)
        self.ffd = FeedForwad(embd_size)
        self.ln1 = nn.LayerNorm(embd_size)
        self.ln2 = nn.LayerNorm(embd_size)

    def forward(self, x):
        x = x + self.sa(self.ln1(x))
        x = x + self.ln2(self.ln2(x))
        return x

class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.embeddding = nn.Embedding(vocab_size,embd_size)
            self.pos_embeddding = nn.Embedding(vocab_size, embd_size)
            self.blocks = nn.Sequential(*[Block(embd_size,num_head) for _ in range(num_layers)])
            self.ln = nn.LayerNorm(embd_size)
            self.lm_head = nn.Linear(embd_size, max_score)

        def forward(self, idx, targets=None):
            B, T  = idx.shape
            tok_embd = self.embeddding(idx)
            temp = torch.arange(T).to(device)
            pos_embd = self.pos_embeddding(temp)
            x = tok_embd+pos_embd
            x = self.blocks(x)
            x = self.ln(x)
            logits = self.lm_head(x)

            if targets==None:
                loss=None
            else :
                B, T, C = logits.shape
                logits = logits.view(B*C, T)
                targets = targets.view(B*C)
                loss = F.cross_entropy(logits, targets)

            return logits, loss


model  = Model().to(device)
optimizer = optim.AdamW(model.parameters(), lr=1e-3)
loss = nn.CrossEntropyLoss()

def eval(val_loader):
  model.eval()
  total_loss = 0
  
  with torch.no_grad():
    for idx_batch, (idx, targets) in enumerate(val_loader):
      idx = idx.to(device)
      targets = targets.to(device)
      logits, loss = model(idx, targets)
      total_loss+=loss

  return total_loss

def train(num_epochs):
    for epoch in range(num_epochs):
      model.train()
      total_loss = 0

      for idx_batch, (idx, targets) in enumerate(train_loader):
        idx = idx.to(device)
        targets = targets.to(device)
        logits, loss = model(idx, targets)
        total_loss+=loss
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

      print(f"epoch : {epoch}")
      print(f"train loss : {total_loss/len(train_loader)}")
      print(f"eval loss: {eval(val_loader)/len(val_loader)}")

if __main__ == '__main__':
  train(100)