# Intro

This repo contains a python script that will autonomously execute a step-and-expose cycle for a 6x6 grid of patterns on a DWL 66+ Laserwriter lithography machine.

You'd think we could use the machine's built in stepping function, but because our dies are individual objects (normally you cut them out of a larger wafer after), we can not use the internal stepper because it naively jogs between dies, and in our case it might crash into the edge of the next die since they can be at slightly different Z heights and also the head auto moves up and down trying to autofocus while moving.

You'd think we could just add a before/after script to move the head up/down between moves, but apparently it is impossible to get any info on the scripting language for this machine :( .

Note that the skeleton for this code was created using [this very nice script recording tool](https://github.com/bigjosh/playwright-script-recorder) that I created exactly for automating tasks over a Chrome Remote Desktop connection.

# Installation

You need...

* Debug version of Chrome 
* Python
* Playwright
* Pillow

# Each burn cycle

## On local computer

1. Start an instance of Chrome with Remote Debugging on port 9992
2. Log into Miles' Googlely account
3. Goto Chrome Remote Desktop and connect to the machine in the clean room. 
4. Make sure that sleep is disabled! A full 36 disk cycle takes about 12 hours and you will be sad if the machine falls asleep in the middle. 

## On laserWriter machine

Do these before starting the script

* Load 36 disks into tray and tray in machine
* Start with machine in LOAD position
* Set up the global alignment panel to option P1
* Alignment set to `Find wafer center optical`
* Make sure the right job is loaded on the Jobs tab (currently `zzzzzzzpf`)
* Pull up the Laser Write software on the RIGHTHAND monitor. Must be full screen

# Start Script

`python real-dwl.py`

The script starts in single step mode so you have to click next for each step.

Once you feel confident that it is working, then you can click the button to run continuously. It will sound an alarm and stop if any tests fail.
