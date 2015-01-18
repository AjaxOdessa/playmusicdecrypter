#!/usr/bin/env python2

# playmusicdecrypter - decrypt MP3 files from Google Play Music offline storage (All Access)
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA

__version__ = "2.0"

import os, sys, struct, re, glob, time, argparse
import Crypto.Cipher.AES, Crypto.Util.Counter
import mutagen
import sqlite3

import superadb


class PlayMusicDecrypter:
    """Decrypt MP3 file from Google Play Music offline storage (All Access)"""
    isencrypted = 1
    def __init__(self, database, infile):
        # Open source file
        self.infile = infile
        self.source = open(infile, "rb")

        # Test if source file is encrypted
        start_bytes = self.source.read(4)
        if start_bytes != "\x12\xd3\x15\x27":
            if start_bytes[0:2] == "\xff\xfb": # MP3
                self.isencrypted = 0
            else:
                raise ValueError("Invalid file format!")

        # Get file info
        self.database = database
        self.info = self.get_info()

    def decrypt(self):
        """Decrypt one block"""
        data = self.source.read(1024)
        if not data:
            return ""

        iv = data[:16]
        encrypted = data[16:]

        if self.isencrypted:
            counter = Crypto.Util.Counter.new(64, prefix=iv[:8], initial_value=struct.unpack(">Q", iv[8:])[0])
            cipher = Crypto.Cipher.AES.new(self.info["CpData"], Crypto.Cipher.AES.MODE_CTR, counter=counter)
            return cipher.decrypt(encrypted)
        else:
            return data

    def decrypt_all(self, outfile=""):
        """Decrypt all blocks and write them to outfile (or to stdout if outfile in not specified)"""
        destination = open(outfile, "wb") if outfile else sys.stdout
        while True:
            decrypted = self.decrypt()
            if not decrypted:
                break

            destination.write(decrypted)
            destination.flush()

    def get_info(self):
        """Returns informations about song from database"""
        db = sqlite3.connect(self.database, detect_types=sqlite3.PARSE_DECLTYPES)
        db.row_factory = sqlite3.Row
        cursor = db.cursor()

        cursor.execute("""SELECT Title, Album, Artist, AlbumArtist, Composer, Genre, Year, Duration,
                                 TrackCount, TrackNumber, DiscCount, DiscNumber, Compilation, CpData
                          FROM music
                          WHERE LocalCopyPath = ?""", (os.path.basename(self.infile),))
        row = cursor.fetchone()
        if row:
            return dict(row)
        else:
            raise ValueError("Empty file info!")

    def normalize_filename(self, filename):
        """Remove invalid characters from filename"""
        return unicode(re.sub(r'[<>:"/\\|?*]', " ", filename))

    def get_outfile(self):
        """Returns output filename based on song informations"""
        destination_dir = os.path.join(self.normalize_filename(self.info["AlbumArtist"]),
                                       self.normalize_filename(self.info["Album"]))
        filename = u"{TrackNumber:02d} - {Title}.mp3".format(**self.info)
        return os.path.join(destination_dir, self.normalize_filename(filename))

    def update_id3(self, outfile):
        """Update ID3 tags in outfile"""
        audio = mutagen.File(outfile, easy=True)
        audio.add_tags()
        audio["title"] = self.info["Title"]
        audio["album"] = self.info["Album"]
        audio["artist"] = self.info["Artist"]
        audio["performer"] = self.info["AlbumArtist"]
        audio["composer"] = self.info["Composer"]
        audio["genre"] = self.info["Genre"]
        audio["date"] = str(self.info["Year"])
        audio["tracknumber"] = str(self.info["TrackNumber"])
        audio["discnumber"] = str(self.info["DiscNumber"])
        audio["compilation"] = str(self.info["Compilation"])
        audio.save()


def is_empty_file(filename):
    """Returns True if file doesn't exist or is empty"""
    return False if os.path.isfile(filename) and os.path.getsize(filename) > 0 else True


def pull_database(destination_dir=".", adb="adb"):
    """Pull Google Play Music database from device"""
    print("Downloading Google Play Music database from device...")
    try:
        adb = superadb.SuperAdb(executable=adb)
    except RuntimeError:
        print("  Device is not connected! Exiting...")
        sys.exit(1)

    if not os.path.isdir(destination_dir):
        os.makedirs(destination_dir)

    db_file = os.path.join(destination_dir, "music.db")
    adb.pull("/data/data/com.google.android.music/databases/music.db", db_file)
    if is_empty_file(db_file):
        print("  Download failed! Exiting...")
        sys.exit(1)


def pull_library(source_dir="/data/data/com.google.android.music/files/music", destination_dir="encrypted", adb="adb"):
    """Pull Google Play Music library from device"""
    print("Downloading encrypted MP3 files from device...")
    try:
        adb = superadb.SuperAdb(executable=adb)
    except RuntimeError:
        print("  Device is not connected! Exiting...")
        sys.exit(1)

    if not os.path.isdir(destination_dir):
        os.makedirs(destination_dir)

    files = [f for f in adb.ls(source_dir) if f.endswith(".mp3")]
    if files:
        start_time = time.time()
        for i, f in enumerate(files):
            sys.stdout.write("\r  Downloading file {}/{}...".format(i + 1, len(files)))
            sys.stdout.flush()
            adb.pull(os.path.join(source_dir, f), os.path.join(destination_dir, f))
        print("")
        print("  All downloads finished ({:.1f}s)!".format(time.time() - start_time))
    else:
        print("  No files found! Exiting...")
        sys.exit(1)


def decrypt_files(source_dir="encrypted", destination_dir=".", database="music.db", remove=0):
    """Decrypt all MP3 files in source directory and write them to destination directory"""
    print("Decrypting MP3 files...")
    if not os.path.isdir(destination_dir):
        os.makedirs(destination_dir)

    files = glob.glob(os.path.join(source_dir, "*.mp3"))
    if files:
        start_time = time.time()
        for f in files:
            try:
                decrypter = PlayMusicDecrypter(database, f)
                print(u"  Decrypting file {} -> {}".format(f, decrypter.get_outfile()))
            except ValueError as e:
                print(u"  Skipping file {} ({})".format(f, e))
                continue

            outfile = os.path.join(destination_dir, decrypter.get_outfile())
            if not os.path.isdir(os.path.dirname(outfile)):
                os.makedirs(os.path.dirname(outfile))

            decrypter.decrypt_all(outfile)
            decrypter.update_id3(outfile)
            if remove:
                os.remove(f)
        print("  Decryption finished ({:.1f}s)!".format(time.time() - start_time))
    else:
        print("  No files found! Exiting...")
        sys.exit(1)


def main():
    # Parse command line options
    parser = argparse.ArgumentParser(description='Decrypt MP3 files from Google Play Music offline storage (All Access)',
                                     usage='%(prog)s [-h] [options] [destination_dir]',
                                     version='%(prog)s {}'.format(__version__))
    parser.add_argument('-a', '--adb', default='adb',
                        help='path to adb executable')
    parser.add_argument('-e','--delete', default = False, action ='store_true',
                        help='Erase files after processing')
    parser.add_argument('-d', '--database',
                        help='local path to Google Play Music database file (will be downloaded from device via adb if not specified)')
    parser.add_argument('-l', '--library',
                        help='local path to directory with encrypted MP3 files (will be downloaded from device via adb if not specified')
    parser.add_argument('-r', '--remote', default='/data/data/com.google.android.music/files/music',
                        help='remote path to directory with encrypted MP3 files on device (default: %(default)s)')
    parser.add_argument('destination_dir', nargs='?', default='.')
    args = parser.parse_args()

    destination_dir = args.destination_dir

    # Download Google Play Music database from device via adb
    if not args.database:
        args.database = os.path.join(destination_dir, "music.db")
        pull_database(destination_dir, adb=args.adb)

    # Download encrypted MP3 files from device via adb
    if not args.library:
        args.library = os.path.join(destination_dir, "encrypted")
        pull_library(args.remote, args.library, adb=args.adb)

    # Decrypt all MP3 files
    decrypt_files(args.library, destination_dir, args.database, args.delete)

if __name__ == "__main__":
    main()