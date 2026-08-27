'''
this ai agent will be able to take long videos and make short videos

enabling this ai agent to help you earn money while you learn to code

you get paid by most streamers to make clips of their streams

i have so much gold in my videos and things that can truly help people

if you want to learn how to code and get paid while you do it

you should just watch my videos and when you see something that you think is gold, you can make a clip out of it

and you can get paid by me for that

check out the #clips channel in discord for more info: https://discord.gg/XAw8US9aHT

examples of people crushing it with my videos:

https://www.youtube.com/@Algonomicstrades
https://www.youtube.com/@moondevonytsnips

they essentially get paid to watch my coding videos and make clips of the good parts

because i dont have time to make clips i can only stream as im building my algos

steps to your success as a moon dev clipper:
1. find good long videos on my channel: https://www.youtube.com/@moondevonyt/videos 
2. watch the video and find the parts that are good
3. make a clip out of it (5 mins-2hours in length)
4. upload the clip to your youtube channel (youtube only, no youtube shorts)
5. when you hit 10,000 views, get paid $69

Standard payout is $69 per 10,000 views but it increases based on your views per month..

10,000 views per month = $69 per 10,000 views
30,000 views per month = $89 per 10,000 views 
50,000 views per month = $100 per 10,000 views
100,000 views per month = $149 per 10,000 views

BONUS GROUP: once you hit 10,000 views per month, you get access to a private channel in discord with training on how to get more views

payments are via crypto on the 1st and 15th of every month, when you hit 10,000 views per month + just email the link, and your tracking of the views to moondevonyt@gmail.com

* if in the USA, you can only earn up to $500 per year but in the rest of the world, you can earn unlimited.

Where do you get the videos from?
1. this dropbox has a bunch of my videos that i will upload to daily: https://www.dropbox.com/scl/fo/d0rjdyus9q3pok5nbmo7b/AM9LOmUDv8KIjmH6ypTALx0?rlkey=klg4tinvneqyui46r6851liwa&st=0zxfym3w&dl=0
2. you have permission to download any of my youtube videos: https://www.youtube.com/@moondevonyt/videos

how do i clip the videos manually?
1. download capcut: https://www.capcut.com/
2. find the interesting parts of the video and make a 5 min - 2 hour clip of it
3. make an interesting thumbnail & title for the clip (you can use my thumbnails if needed, just google how to download)
4. post on youtube
5. when you hit 10,000 views email your link and stats to moondevonyt@gmail.com to get paid

how do i clip videos with this agent?
1. put in my long videos into this folder path src/data/videos/raw_clips
2. make sure to have this folder too src/data/videos/finished_vids this is the output folder
3. run the code and wait for them to output to the above folder
4. make a good thumbnail and title then upload to youtube
5. wait for 10,000 views and email the link and stats to moondevonyt@gmail.com to get paid

here is another training video, the only difference is that we only accept long youtube videos now. the below training is helpful though.

training video to learn how to clip videos: https://www.youtube.com/watch?v=nqWax0EPkcs

all of my videos you can use: https://www.dropbox.com/scl/fo/d0rjdyus9q3pok5nbmo7b/AM9LOmUDv8KIjmH6ypTALx0?rlkey=klg4tinvneqyui46r6851liwa&st=0zxfym3w&dl=0
- you can also download the videos from my channel: https://www.youtube.com/@moondevonyt/videos
'''

# Moon Dev's Video Splitter Agent🎬
import sys
from pathlib import Path
import os
import moviepy.editor as mp
import time
from termcolor import cprint
from tqdm import tqdm
import subprocess
import shutil
import random

# Add project root to Python path for imports
project_root = str(Path(__file__).parent.parent.parent)
if project_root not in sys.path:
    sys.path.append(project_root)

# Constants
MIN_CLIP_DURATION = 300  # 5 minutes in seconds
MAX_CLIP_DURATION = 1200  # 20 minutes in seconds

 # get some videos from here and put in input_dir foder: https://www.dropbox.com/scl/fo/d0rjdyus9q3pok5nbmo7b/AM9LOmUDv8KIjmH6ypTALx0?rlkey=klg4tinvneqyui46r6851liwa&st=0zxfym3w&dl=0
INPUT_DIR = Path("/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/videos/raw_clips")
   
OUTPUT_DIR = Path("/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/videos/finished_clips")
TEMP_DIR = Path("/Users/md/Dropbox/dev/github/moon-dev-ai-agents-for-trading/src/data/videos/temp")

class ClipsAgent:
    def __init__(self):
        """Initialize the Clips Agent"""
        self._setup_directories()
        cprint("🎬 Moon Dev's Clips Agent initialized!", "green")
        
    def _setup_directories(self):
        """Ensure input and output directories exist"""
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        cprint(f"📂 Input directory: {INPUT_DIR}", "cyan")
        cprint(f"📂 Output directory: {OUTPUT_DIR}", "cyan")
        cprint(f"📂 Temp directory: {TEMP_DIR}", "cyan")
    
    def _get_video_files(self):
        """Get list of video files from input directory"""
        video_extensions = ['.mp4', '.mov', '.avi', '.mkv']
        video_files = []
        for ext in video_extensions:
            video_files.extend(list(INPUT_DIR.glob(f'*{ext}')))
        return video_files

    def _get_video_duration(self, video_path):
        """Get video duration using ffprobe"""
        try:
            cmd = [
                'ffprobe', 
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                str(video_path)
            ]
            output = subprocess.check_output(cmd).decode().strip()
            return float(output)
        except Exception as e:
            cprint(f"❌ Error getting duration: {str(e)}", "red")
            return None

    def _split_video_ffmpeg(self, video_path, start_time, end_time, output_path):
        """Split video using ffmpeg directly"""
        try:
            cmd = [
                'ffmpeg',
                '-i', str(video_path),
                '-ss', str(start_time),
                '-t', str(end_time - start_time),
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-y',  # Overwrite output file if it exists
                str(output_path)
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            cprint(f"❌ FFmpeg error: {e.stderr.decode()}", "red")
            return False
        except Exception as e:
            cprint(f"❌ Error: {str(e)}", "red")
            return False

    def _get_random_clip_duration(self):
        """Get a random clip duration between MIN and MAX"""
        return random.randint(MIN_CLIP_DURATION, MAX_CLIP_DURATION)
    
    def split_video(self, video_path):
        """Split a single video into clips of random durations"""
        try:
            # Get video duration
            cprint(f"\n🎥 Loading video: {video_path.name}", "cyan")
            duration = self._get_video_duration(video_path)
            
            if duration is None:
                cprint(f"❌ Could not get duration for {video_path.name}", "red")
                return
            
            if duration < MIN_CLIP_DURATION:
                cprint(f"⚠️ Video {video_path.name} is too short (< {MIN_CLIP_DURATION}s), skipping...", "yellow")
                return
            
            # Calculate clip boundaries
            clip_boundaries = []
            current_time = 0
            clip_count = 0
            
            while current_time < duration:
                clip_duration = self._get_random_clip_duration()
                end_time = min(current_time + clip_duration, duration)
                
                # Only add if the remaining clip is long enough
                if end_time - current_time >= MIN_CLIP_DURATION:
                    clip_boundaries.append((current_time, end_time))
                    clip_count += 1
                
                current_time = end_time
                
            cprint(f"⏱️ Duration: {duration:.2f}s", "cyan")
            cprint(f"✂️ Creating {clip_count} clips of random lengths ({MIN_CLIP_DURATION}s to {MAX_CLIP_DURATION}s)...", "cyan")
            
            successful_clips = 0
            for i, (start_time, end_time) in enumerate(clip_boundaries, 1):
                # Create output path
                output_path = OUTPUT_DIR / f"{video_path.stem}_clip_{i}.mp4"
                
                # Extract clip
                clip_duration = end_time - start_time
                cprint(f"\n✂️ Extracting clip {i} ({start_time:.1f}s to {end_time:.1f}s, duration: {clip_duration:.1f}s)", "cyan")
                cprint(f"💾 Saving to: {output_path.name}", "cyan")
                
                if self._split_video_ffmpeg(video_path, start_time, end_time, output_path):
                    successful_clips += 1
                    cprint(f"✨ Created: {output_path.name}", "green")
                else:
                    cprint(f"❌ Failed to create: {output_path.name}", "red")
            
            cprint(f"✅ Finished processing {video_path.name} ({successful_clips}/{clip_count} clips created)", "green")
            
        except Exception as e:
            cprint(f"❌ Error processing {video_path.name}: {str(e)}", "red")
    
    def run(self):
        """Main processing loop"""
        cprint("\n🎬 Moon Dev's Clips Agent starting...", "cyan")
        cprint(f"⚙️ Min clip duration: {MIN_CLIP_DURATION}s (5 mins)", "cyan")
        cprint(f"⚙️ Max clip duration: {MAX_CLIP_DURATION}s (20 mins)", "cyan")
        
        while True:
            try:
                video_files = self._get_video_files()
                
                if not video_files:
                    cprint("😴 No videos found, sleeping for 10 seconds...", "yellow")
                    time.sleep(10)
                    continue
                
                for video_path in video_files:
                    self.split_video(video_path)
                    # Optional: Move processed video to a 'processed' folder
                    
            except KeyboardInterrupt:
                cprint("\n👋 Clips Agent shutting down gracefully...", "yellow")
                # Clean up temp directory
                if TEMP_DIR.exists():
                    shutil.rmtree(TEMP_DIR)
                break
            except Exception as e:
                cprint(f"❌ Error in main loop: {str(e)}", "red")
                time.sleep(5)

if __name__ == "__main__":
    try:
        agent = ClipsAgent()
        agent.run()
    except KeyboardInterrupt:
        cprint("\n👋 Clips Agent shutting down gracefully...", "yellow")
        # Clean up temp directory
        if TEMP_DIR.exists():
            shutil.rmtree(TEMP_DIR)
    except Exception as e:
        cprint(f"\n❌ Fatal error: {str(e)}", "red")

