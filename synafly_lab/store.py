"""Bounded content-addressed storage; fast-forward only, never silent fork choice."""
from pathlib import Path
import sqlite3
import threading
from .canonical import Invalid,canonical,digest,parse

class Conflict(Invalid): pass

class Store:
    def __init__(self,path,run):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.run=run;self.lock=threading.RLock()
        self.db=sqlite3.connect(path,check_same_thread=False,isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS objects (hash TEXT PRIMARY KEY,payload BLOB NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        try:
            with self.transaction():
                existing=self.db.execute("SELECT value FROM meta WHERE key='run'").fetchone()
                if existing and existing[0]!=run.id: raise Invalid('Database belongs to another run')
                if not existing:
                    g=run.genesis();h=digest(g);self._insert(g)
                    self.db.execute("INSERT INTO meta VALUES ('run',?)",(run.id,))
                    self.db.execute("INSERT INTO meta VALUES ('head',?)",(h,))
                self.verify_history()
        except BaseException: self.db.close();raise
    def transaction(self):
        from contextlib import contextmanager
        @contextmanager
        def tx():
            with self.lock:
                self.db.execute('BEGIN IMMEDIATE')
                try: yield; self.db.execute('COMMIT')
                except BaseException: self.db.execute('ROLLBACK');raise
        return tx()
    def close(self):
        with self.lock:self.db.close()
    def _insert(self,payload):
        h=digest(payload)
        if self.db.execute('SELECT 1 FROM objects WHERE hash=?',(h,)).fetchone():return h
        if self.db.execute('SELECT count(*) FROM objects').fetchone()[0]>=2048:raise Invalid('Store capacity reached')
        self.db.execute('INSERT INTO objects VALUES (?,?)',(h,canonical(payload)));return h
    def contains(self,h):
        with self.lock:return self.db.execute('SELECT 1 FROM objects WHERE hash=?',(h,)).fetchone() is not None
    def get(self,h):
        with self.lock:
            row=self.db.execute('SELECT payload FROM objects WHERE hash=?',(h,)).fetchone()
            if not row:raise Invalid('Unknown checkpoint')
            p=parse(row[0])
            if digest(p)!=h:raise Invalid('Stored content hash mismatch')
            return p
    def head(self):
        with self.lock:
            h=self.db.execute("SELECT value FROM meta WHERE key='head'").fetchone()[0]
            return {'hash':h,'checkpoint':self.get(h)}
    def verify_history(self):
        with self.lock:
            current=self.head(); chain=[]
            for _ in range(2048):
                p=current['checkpoint'];chain.append(p)
                if p.get('sequence')==0:break
                current={'checkpoint':self.get(p['parent'])}
            else:raise Invalid('History limit')
            parent=None
            for p in reversed(chain): self.run.verify(p,parent);parent=p
            return len(chain)
    def advance(self,inputs,expected_parent):
        with self.transaction():
            head=self.head()
            if head['hash']!=expected_parent:raise Conflict('Head changed; explicit retry required')
            p=self.run.advance(head['checkpoint'],inputs);h=self._insert(p)
            self.db.execute("UPDATE meta SET value=? WHERE key='head'",(h,))
            return {'hash':h,'checkpoint':p}
    def import_chain(self,envelopes):
        if not 1<=len(envelopes)<=256:raise Invalid('Sync batch limit')
        with self.transaction():
            head=self.head();parent=head['checkpoint'];added=0
            for env in envelopes:
                if type(env) is not dict or set(env)!={'hash','checkpoint'}:raise Invalid('Envelope')
                p=env['checkpoint'];h=env['hash']
                if digest(p)!=h:raise Invalid('Peer content hash mismatch')
                if h==digest(parent):continue
                if p.get('parent')!=digest(parent):raise Conflict('Not a fast-forward; fork or stale history')
                self.run.verify(p,parent);self._insert(p);parent=p;added+=1
            h=digest(parent);self.db.execute("UPDATE meta SET value=? WHERE key='head'",(h,))
            return {'hash':h,'imported':added}
