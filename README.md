beets-rules
===========
A beets plugin to store a set of rules in the config

This is VERY early alpha-quality software. don't expect it to run flawlessly.


## Config ##

### Rules ###
The "rules" config is a list of "beets modify"-style rules to be applied
either when calling "beets rules-apply" or automatically on import.

    rules:
        - genre:Classic rating=-100
        - genre:Metal rating=0
        - "genre:Love Pop" rating=1000

### Plugin config ###
The plugin configuration section has a few settings:

#### showchanges
Wether changes should be shown when using "beets rules-apply" (default: yes)

#### confirm
Wether the shown changes need to be confirmed by the user (default: yes)

#### write
Write the new values to file metadata? (default: yes)

#### move
Move the files to a new location if the computed path chages as a result of a rule (default: yes)

#### onimport
Automatically apply rules on newly-imported files (default: no)

