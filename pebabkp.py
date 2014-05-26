#!/usr/bin/python
import os
import bz2
import sys
import time
import shutil
import tarfile
import argparse
import paramiko
import subprocess
import ConfigParser

# Global Variables
config = ConfigParser.RawConfigParser()
config.read('/etc/pebabkp.conf')
home = os.path.expanduser('~')
host_bkp = config.get('global', 'host_bkp')
path_remote_dir = config.get('global', 'path_remote_dir')
user = config.get('global', 'user')
auth_method = config.get('global', 'auth_method')
port = config.getint('global', 'port_bkp')
date = time.strftime("%Y-%m-%d_%H%M%S")
local_dir = config.get('global', 'local_dir')
file_redis = config.get('redis', 'file_redis')
redis_bkp_dir = config.get('redis', 'redis_bkp_dir')
bkp_diary = config.get('dir', 'diary')

# Start Function
def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('-r', '--redis',  help='Make a Redis Backup', action='store_true')
  parser.add_argument('-p', '--postgres', help='Make a PostgreSQL Backup', action='store_true')
  parser.add_argument('-c', '--remote', help='Copy bkps to remote', action='store_true')
  parser.add_argument('-s', '--s3', help='Prepare copy to Amazon S3', action='store_true')
  args = parser.parse_args()
  if args.redis and args.postgres:
    redis_dump = backup_redis(date)
    psql_dump = backup_postgres(date)
  elif args.redis:
    redis_dump = backup_redis(date)
  elif args.postgres:
    psql_dump = backup_postgres(date)
  elif args.s3:
      print "Use this option only with -c, -p and -r"
  else:
    parser.print_help()
  if args.remote and not (args.redis and args.postgres):
    parser.print_help()
  elif args.remote and args.redis and args.postgres and args.s3:
    path_remote_redis_dir = config.get('redis', 'remote_redis_dir')
    path_remote_postgres_dir = config.get('postgres', 'remote_postgres_dir')
    remote_file_redis = '%s/%s' % (path_remote_redis_dir, os.path.basename(redis_dump))
    remote_file_postgres = '%s/%s' % (path_remote_postgres_dir, os.path.basename(psql_dump))
    transfer_sftp(host_bkp, port, user, redis_dump, remote_file_redis)
    transfer_sftp(host_bkp, port, user, psql_dump, remote_file_postgres)
    shutil.copy(redis_dump, bkp_diary)
    shutil.copy(psql_dump, bkp_diary)
  elif args.remote and args.redis:
    path_remote_dir = config.get('redis', 'remote_redis_dir')
    remote_file = '%s/%s' %(path_remote_dir, os.path.basename(redis_dump))
    transfer_sftp(host_bkp, port, user, redis_dump, remote_file)
  elif args.remote and args.postgres:
    path_remote_dir = config.get('postgres', 'remote_postgres_dir')
    remote_file = '%s/%s' % (path_remote_dir, os.path.basename(psql_dump))
    print remote_file
    transfer_sftp(host_bkp, port, user, psql_dump, remote_file)

# Create a local backup directory
def create_bkp_dir(bkp_dir):
  if not os.path.exists(bkp_dir):
    os.makedirs(bkp_dir)

# compress dumps with bz2
def compress(dump, dest, date):
  if os.path.exists(dump):
    if os.path.isfile(dump):
      data = open(dump, 'r').read()
      compress = bz2.BZ2File('%s.bz2' % (dump), 'wb')
      compress.write(data)
      compress.close()
      os.remove(dump)
      dump = compress.name
      return dump
    elif os.path.isdir(dump):
      compress = tarfile.open('%s/%s-%s.tar.bz2' % (dest, dump, date), 'w:bz2')
      compress.add(dump)
      compress.close()
  else:
    print "File doesn't exist"

# Function to dump and compress postgres
def backup_postgres(date):
  user = config.get('postgres', 'user')
  psql_host = config.get('postgres', 'host')
  database = config.get('postgres', 'database')
  postgres_bkp_dir = config.get('postgres', 'postgres_bkp_dir')
  password = config.get('postgres', 'pass')
  create_bkp_dir(postgres_bkp_dir)
  if password == 'NO_PASSWD':
    try:
      cmd = subprocess.Popen(['/usr/bin/pg_dump', '-U', user, '-w', '-h', psql_host, '-O', database, '-f', '%s/%s-%s.sql' % (postgres_bkp_dir, database, date)], subprocess.PIPE).wait()
      dump = '%s/%s-%s.sql' % (postgres_bkp_dir, database, date)
      dump_compress = compress(dump, postgres_bkp_dir, date)
      return dump_compress
    except IOError:
      print "ERROR - Probably your pg_hba.conf is set to md5 and your /etc/pebabkp.conf with NO_PASSWD, please fix it"
  elif password <> 'NO_PASSWD':
    cmd = subprocess.Popen(['PGPASSWORD=%s /usr/bin/pg_dump -U %s -h %s -O %s -f %s/%s-%s.sql' % (password, user, psql_host, database, postgres_bkp_dir, database, date)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, shell=True).wait()
    dump = '%s/%s-%s.sql' % (postgres_bkp_dir, database, date)
    dump_compress = compress(dump, postgres_bkp_dir, date)
    return dump_compress

# Function to dump and compress redis
def backup_redis(date):
  create_bkp_dir(redis_bkp_dir)
  dump = redis_bkp_dir+'/dump-%s.rdb' % date
  shutil.copyfile(file_redis, dump)
  dump_compress = compress(dump, redis_bkp_dir, date)
  return dump_compress

# Function to dump and compress directory
def directory(date):
  source_dir = config.get('dir', 'directories')
  dest_dir = config.get('dir', 'dest_dir')
  create_bkp_dir(dest_dir)
  compress(source_dir, dest_dir, date)


# Function to tranfers dumps to a remote server with sftp
def transfer_sftp(host, port, user, source, dest):
  conn = paramiko.SSHClient()
  conn.set_missing_host_key_policy(paramiko.AutoAddPolicy())
  remote_dir_bkp = os.path.dirname(dest)
  try:
    if auth_method == 'key':
      key = paramiko.RSAKey.from_private_key_file(config.get('global','key'))
      conn.connect(host, port, username=user, pkey=key)
      sftp = conn.open_sftp()
      try:
        sftp.put(source, dest)
      except IOError:
        print "Remote directory does not exist, please look at /etc/pebabkp.conf and create"
    elif auth_method == 'password':
      passwd = config.get('global', 'pass')
      conn.connect(host, port, username=user, password=passwd)
      sftp = conn.open_sftp()
      try:
        sftp.put(source, dest)
      except IOError:
        print "Remote directory does not exist, please look at /etc/pebabkp.conf and create"
  except paramiko.AuthenticationException:
    print "Please, set a public key in remote server or password in /etc/pebabkp.conf"
  except paramiko.BadAuthenticationType:
    print "Bad Authentication Type"

if __name__ == "__main__":
  main()
