#!/usr/bin/python
# -*- coding: utf-8 -*-

import codecs
import datetime
import json
import logging
import os
import pika
import psycopg2
import select
import signal
import sys
import time
import types

#for libretime, there is no interactive way to define the watch dir
#insert into cc_music_dirs (directory,type,exists,watched) values ('/srv/airtime/watch','watched','t','t');


# definitions RabbitMQ
EXCHANGE = "airtime-watch"
EXCHANGE_TYPE = "topic"
ROUTING_KEY = ""
QUEUE = "airtime-watch"


# create empty dictionary 
database = {}
# keep the program running
shutdown=False

#
# logging
#
logging.basicConfig(format='%(asctime)s %(message)s',filename='/var/log/airtime/libretime_watch.log',level=logging.INFO)

def update_database (conn):
   cur = conn.cursor()
   cols = database.keys()
   cols_str = str(cols)
   #cut off enclosing []
   cols_str = cols_str[1:-1]
   cols_str = cols_str.replace("'","")
   vals = [database[x] for x in cols]
   vals_str_list = ["%s"] * len(vals)
   vals_str = ", ".join(vals_str_list)
   cur.execute ("UPDATE cc_files set ({cols}) = ({vals_str}) where directory = {dir} and filepath ='{file}'"
       .format( cols = cols_str, vals_str = vals_str, dir = database["directory"], file = database["filepath"] ), vals)
   conn.commit()
   cur.close()


def insert_database (conn):
   cur = conn.cursor()
   cols = database.keys()
   cols_str = str(cols)
   #cut off enclosing []
   cols_str = cols_str[1:-1]
   cols_str = cols_str.replace("'","")
   vals = [database[x] for x in cols]
   vals_str_list = ["%s"] * len(vals)
   vals_str = ", ".join(vals_str_list)
   cur.execute ("INSERT INTO cc_files ({cols}) VALUES ({vals_str})".format(
           cols = cols_str, vals_str = vals_str), vals)
   conn.commit()
   cur.close()

def analyse_file (filename):
   """This method analyses the file and returns analyse_ok 
      It's filling the database dictionary with metadata read from
      the file
   """
   import hashlib
   import magic
   # test
   from mimetypes import MimeTypes
   from mutagen.easyid3 import EasyID3
   from mutagen.mp3 import MP3

   analyse_ok=False
   logging.info ("analyse Filename: "+filename)
   #try to determin the filetype 
   mime_check = magic.from_file(filename, mime=True)
   database["mime"] = mime_check
   # test
   f = MP3(filename)
   mime_mutagen = f.mime[0]
   mime = MimeTypes()
   type, a = mime.guess_type(filename)
   logging.info ("mime_check :"+database["mime"]+ " mime: "+type+" mutagen: " +mime_mutagen )
   #
   database["ftype"] = "audioclip"
   database["filesize"] = os.path.getsize(filename) 
   database["import_status"]=0
   #md5
   with open(filename, 'rb') as fh:
       m = hashlib.md5()
       while True:
           data = fh.read(8192)
           if not data:
              break
           m.update(data)
       database["md5"] = m.hexdigest()
   # MP3 file ?
   if database["mime"] in ['audio/mpeg','audio/mp3','application/octet-stream']:
     try:
       audio = EasyID3(filename)
       database["track_title"]=audio['title'][0]
       try:
         database["artist_name"]=audio['artist'][0]
       except StandardError, err:
         logging.warning('no title ID3 for '+filename) 
         database["artist_name"]= ""       
       try:
         database["genre"]=audio['genre'][0]
       except StandardError, err:
         logging.warning('no genre ID3 for '+filename) 
         database["genre"]= ""
       try:
         database["album_title"]=audio['album'][0]
       except StandardError, err:
         database["album_title"]= ""
       # get data encoded into file
       f = MP3(filename)
       database["bit_rate"]=f.info.bitrate
       database["sample_rate"]=f.info.sample_rate
       if hasattr(f.info, "length"):
         #Converting the length in seconds (float) to a formatted time string
         track_length = datetime.timedelta(seconds=f.info.length)
         database["length"] = str(track_length) #time.strftime("%H:%M:%S.%f", track_length)
         # Other fields for Airtime
         database["cueout"] = database["length"]
       database["cuein"]= "00:00:00.0"
       # use mutage to get better mime 
       if  f.mime:
            database["mime"] = f.mime[0]
       if database["mime"] in ["audio/mpeg", 'audio/mp3']:
          if f.info.mode == 3:
                database["channels"] = 1
          else:
                database["channels"] = 2
       else:
            database["channels"] = f.info.channels
       analyse_ok=True

     except StandardError, err:
          logging.error('Error ',str(err),filename) 
          #print "Error: ",str(err),filename
   return analyse_ok

def connect_database(): 
  try:
    conn = psycopg2.connect("dbname='airtime' user='airtime' host='localhost' password='airtime'")
  except:
    logging.critical('Unable to connect to the database') 
    #print "I am unable to connect to the database"
  return conn


def watch (dir_id):
    logging.info ("Start scanning Dir ID: "+str(dir_id))
    # look for what dir we've to watch
    conn = connect_database()
    cur = conn.cursor()
    try:
      cur.execute ("SELECT directory from cc_music_dirs where id = '"+dir_id+"'")
      row = cur.fetchone()
      watch_dir = row[0]+"/"
      len_watch_dir = len(watch_dir) 
      cur.close()
    except:
      logging.critical("Can't get directory for watching") 
      #print ("Can't get directory for watching")
      exit()
 
    # so now scan all directories
    for curroot, dirs, files in os.walk(watch_dir):
        if files == None:
          continue
        for curFile in files:
          #database = {}
          database["directory"] = dir_id 
          curFilePath = os.path.join(curroot,curFile)
          # cut off the watch_dir
          database["filepath"] = curFilePath[len_watch_dir:]
          # get modification date
          database["mtime"] = time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(int(os.path.getmtime(curFilePath))))
          # prepare database 
          cur = conn.cursor()
          #file already in database
          try:
            cur.execute ("SELECT count(*) from cc_files where"
                +" filepath = '"+database["filepath"]+"'" 
                +" and directory = "+str(database["directory"]))
          except: 
            logging.warning ("I can't SELECT count(*) ... from cc_files")
            print "I can't SELECT from cc_files"
          row = cur.fetchone()
          # is there already a record
          if row[0] == 0:
            logging.info("Insert: "+database["filepath"])
            #print ("Insert: "+database["filepath"])
            database["utime"] = datetime.datetime.now()
            if analyse_file (curFilePath):
              insert_database (conn)
            #let's sleep
#            time.sleep(1)
          else :
            cur1 = conn.cursor()
            try:
              # look for mtime
              cur1.execute ("SELECT mtime from cc_files where"
                +" filepath = '"+database["filepath"]+"'" 
                +" and directory = "+str(database["directory"]))
            except:
              logging.warning ("I can't SELECT mtime ... from cc_files")
              #print "I can't SELECT from cc_files"
            row = cur1.fetchone()
            # update needs only called, if mtime different
            if str(row[0]) != database["mtime"]:
               logging.info("Update: "+database["filepath"])
               #print ("Update "+database["filepath"])
               database["utime"] = datetime.datetime.now()
               if analyse_file (curFilePath):
                 update_database (conn)
            cur1.close()
          cur.close()
    #
    # close database session
    conn.close() 
    logging.info ("Scan finished..")

################################################################
# RabbitMQ parts
################################################################
def graceful_shutdown(self, signum, frame):
   '''Disconnect and break out of the message listening loop'''
   shutdown = True

def connect_to_messaging_server():
  """Connect to RabbitMQ Server and start listening for messages. 
     Returns RabbitMQ connection and channel
  """
  credentials=pika.credentials.PlainCredentials('airtime', 'airtime')
  connection = pika.BlockingConnection(pika.ConnectionParameters(host='localhost',
            virtual_host='/airtime',credentials=credentials))
  channel = connection.channel()
  #channel.exchange_delete (exchange=EXCHANGE)
  channel.exchange_declare(exchange=EXCHANGE, type=EXCHANGE_TYPE, durable=True)
  #channel.queue_delete(queue=QUEUE)
  result = channel.queue_declare(queue=QUEUE, durable=True)
  channel.queue_bind(exchange=EXCHANGE, queue=QUEUE, routing_key=ROUTING_KEY)

  logging.info("Listening for messages...")
  channel.basic_consume(msg_received_callback,queue=QUEUE, no_ack=False)

  return connection, channel

def msg_received_callback (channel, method, properties,body):
  '''Message reader'''
  try:
    msg_dict = json.loads(body)
    #api_key         = msg_dict["api_key"]
    #callback_url    = msg_dict["callback_url"]

    #audio_file_path = msg_dict["tmp_file_path"]
    #import_directory = msg_dict["import_directory"]
    #original_filename = msg_dict["original_filename"]
    #file_prefix = msg_dict["file_prefix"]
    #storage_backend = msg_dict["storage_backend"]
    if "rescan_watch" in msg_dict["cmd"]: 
       # now call the watching routine 
       logging.info ("Got message: "+msg_dict["cmd"]+" ID: "+msg_dict["id"])
       watch(msg_dict["id"]) 
    else :
       logging.info ("Got unhandled message: "+body)
  except Exception as e:
    logging.error("No JSON received: "+body+ str(e))
  channel.basic_ack(delivery_tag = method.delivery_tag)

def wait_for_messages(channel):
  """Waiting for messages comming from RabbitMQ
  """
  channel.start_consuming()

def disconnect_from_messaging_server(connection):
  """Disconnect RabbitMQ"""
  connection.close()

def main():
  # Set up a signal handler so we can shutdown gracefully
  # For some reason, this signal handler must be set up here. I'd rather 
  # put it in AirtimeAnalyzerServer, but it doesn't work there (something to do
  # with pika's SIGTERM handler interfering with it, I think...)
  signal.signal(signal.SIGTERM, graceful_shutdown)

  while not shutdown:
    try:
       connection, channel = connect_to_messaging_server()
       wait_for_messages(channel)
    except (KeyboardInterrupt, SystemExit):
       break # Break out of the while loop and exit the application
    except select.error:
      pass
    except pika.exceptions.AMQPError as e:
       if shutdown:
          break
       logging.error("Connection to message queue failed. ")
       logging.error(e)
       logging.info("Retrying in 5 seconds...")
       time.sleep(5)
  # end of loop
  disconnect_from_messaging_server(connection)
  logging.info("Exiting cleanly.")


if __name__ == "__main__":
    logging.info("Program started..")
    main()
