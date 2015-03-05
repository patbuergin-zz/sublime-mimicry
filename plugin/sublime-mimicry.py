import json
import sublime, sublime_plugin
from struct import unpack_from
from os import path
from urllib.request import urlopen
from subprocess import Popen, PIPE

SUBLIME_PREF = 'Preferences.sublime-settings'
TARGET_FILE = 'Test.tmTheme'
PLUGIN_PATH = 'Packages/sublime-mimicry/'
PLUGIN_PATH_FULL = path.dirname(path.realpath(__file__))
SPOTIFY_TRACK_URI = 'https://api.spotify.com/v1/tracks/{track_id}'
COVER_RAW_PATH = path.join(PLUGIN_PATH_FULL, '64x64.jpg')
COVER_BMP_PATH = path.join(PLUGIN_PATH_FULL, 'out.bmp')
ENHANCE_BRIGHTNESS_THRESHOLD = 200
ENHANCE_BRIGHTNESS_BOOST = 75
OUT_THEME = path.join(PLUGIN_PATH_FULL, TARGET_FILE)

class ColorCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        state = get_spotify_state()
        current_track = state['track_id']
        # e.g. spotify:track:5tUzlFYuhwdWz5Ln1GCauC
        if not current_track.startswith('spotify:track:'):
            # e.g. a track saved locally
            pass
        else:
            current_track_id = current_track.split(':')[-1]

            uri = SPOTIFY_TRACK_URI.format(track_id=current_track_id)

            track_data_raw = urlopen(uri).read()
            track_data = json.loads(track_data_raw.decode('utf-8'))

            # the api currently returns three different image sizes in descending
            # order - take the smallest one (64x64)
            cover_uri = track_data['album']['images'][-1]['url']
            # save the cover image to disk
            cover_data = urlopen(cover_uri)
            with open(COVER_RAW_PATH, 'wb') as raw:
                raw.write(cover_data.read())

            # convert the 64x64 jpeg image to a 16x16 bmp with 8 colors
            # introduces dependency on libjpeg (+ djpeg)
            cmd = '/usr/local/bin/djpeg -scale 5/32 -colors 8 -bmp -outfile "%s" "%s"' \
                  % (COVER_BMP_PATH, COVER_RAW_PATH)
            djpeg = Popen(cmd, stdout=PIPE, stderr=PIPE, cwd=PLUGIN_PATH_FULL, shell=True)
            djpeg.communicate()

            # extract colors and counts from the bmp file
            with open(COVER_BMP_PATH, 'rb') as bmp:
                data = bytearray(bmp.read())

            # get byte offset to PixelArray (bfOffBits)
            pixel_offset = unpack_from('<I', data[10:14])[0]

            # extract rgb values from the color table
            color_table_offset = 14+40 # BITMAPFILEHEADER & BITMAPV5HEADER
            color_table_length = 8*4   # 8 colors * 0bBBGGRR00
            color_table_end = color_table_offset + color_table_length
            rgbs = []
            for i in range(color_table_offset, color_table_end, 4):
                rgbs.append([
                    unpack_from('<B', data[i+2:i+3])[0],
                    unpack_from('<B', data[i+1:i+2])[0],
                    unpack_from('<B', data[i+0:i+1])[0]
                ])

            color_hist = [0] * len(rgbs)
            for i in range(pixel_offset, len(data)):
                color_hist[data[i]] += 1

            theme = generate_theme(rgbs, color_hist)

            with open(OUT_THEME, 'w') as out:
                out.write(theme)

            set_theme(path.join(PLUGIN_PATH, TARGET_FILE))


def get_spotify_state():
    cmd = '/usr/bin/osascript get_state.applescript'
    get_state = Popen(cmd, cwd=PLUGIN_PATH_FULL, stdout=PIPE, stderr=PIPE, shell=True)
    state_raw, _ = get_state.communicate()
    # the script returns a json string, e.g.
    # {
    #   "track_id": "spotify:track:1cownN6zH1tYEZJbx1MVB0",
    #   "volume": 100,
    #   "position": 22,
    #   "state": "playing"
    # }
    return json.loads(state_raw.decode('utf-8'))


# Helpers for dealing with colors
def to_hex(rgb):
    return '#%02x%02x%02x' % (rgb[0], rgb[1], rgb[2])

def lighten(rgb, intensity, limit=255):
    return [min(c+intensity, limit) for c in rgb]

def darken(rgb, intensity, limit=0):
    return [max(c-intensity, limit) for c in rgb]

def mix(rgb, mixin, factor=0.1):
    rem = 1.0 - factor
    return [int(rgb[0]*rem + mixin[0]*factor),
            int(rgb[1]*rem + mixin[1]*factor),
            int(rgb[2]*rem + mixin[2]*factor)]

def enhance(rgb):
    if sum(rgb) < ENHANCE_BRIGHTNESS_THRESHOLD:
        return lighten(rgb, ENHANCE_BRIGHTNESS_BOOST)
    else:
        return rgb


def get_current_theme():
    return sublime.load_settings(SUBLIME_PREF).get('color_scheme', '')

def set_theme(theme_path):
    return sublime.load_settings(SUBLIME_PREF).set('color_scheme', theme_path)

def generate_theme(rgbs, color_hist):
    popularity_desc = [i[0] for i in sorted(enumerate(color_hist), key=lambda x: x[1], reverse=True)]
    brightness_desc = [i[0] for i in sorted(enumerate(rgbs), key=lambda x: x[1], reverse=True)]

    lightest = rgbs[brightness_desc[0]]
    darkest = rgbs[brightness_desc[-1]]

    popularity_desc.remove(brightness_desc[0])
    popularity_desc.remove(brightness_desc[-1])

    primary = enhance(rgbs[popularity_desc[0]])
    secondary = enhance(rgbs[popularity_desc[1]])
    ternary = enhance(rgbs[popularity_desc[2]])

    foreground = mix([225, 225, 225], lightest)
    caret = lighten(primary, 20)
    background = mix([30, 30, 30], darkest)
    line_highlight = lighten(background, 20)
    selection = lighten(darkest, 25)
    comment = lighten(line_highlight, 60)
    comment_special = lighten(comment, 20)
    keyword_constant = darken(primary, 10)
    keyword_type = primary
    builtin_pseudo = secondary
    name_class = lighten(ternary, 40)
    name_constant = lighten(ternary, 20)
    name_function = lighten(secondary, 30)
    name_variable = lighten(primary, 5)
    tag_name = secondary
    tag_punctuation = darken(secondary, 35)
    literal_number = lighten(secondary, 65)
    literal_string = darken(foreground, 45)
    literal_regex = darken(foreground, 65)

    return THEME_TEMPLATE.format(
        background=to_hex(background),
        caret=to_hex(caret),
        foreground=to_hex(foreground),
        line_highlight=to_hex(line_highlight),
        selection=to_hex(selection),
        comment=to_hex(comment),
        comment_special=to_hex(comment_special),
        keyword_constant=to_hex(keyword_constant),
        keyword_type=to_hex(keyword_type),
        builtin_pseudo=to_hex(builtin_pseudo),
        name_class=to_hex(name_class),
        name_constant=to_hex(name_constant),
        name_function=to_hex(name_function),
        name_variable=to_hex(name_variable),
        tag_name=to_hex(tag_name),
        tag_punctuation=to_hex(tag_punctuation),
        literal_number=to_hex(literal_number),
        literal_string=to_hex(literal_string),
        literal_regex=to_hex(literal_regex)
    )

THEME_TEMPLATE = """
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>author</key>
  <string>@patbuergin - structure derived from mbixby/facebook-color-scheme</string>
  <key>name</key>
  <string>sublime-mimicry</string>
  <key>settings</key>
  <array>
    <dict>
      <key>settings</key>
      <dict>
        <key>background</key>
        <string>{background}</string>
        <key>caret</key>
        <string>{caret}</string>
        <key>foreground</key>
        <string>{foreground}</string>
        <key>invisibles</key>
        <string>#00FF00</string>
        <key>lineHighlight</key>
        <string>{line_highlight}</string>
        <key>selection</key>
        <string>{selection}</string>
        <key>selectionBorder</key>
        <string>{selection}</string>
      </dict>
    </dict>
    <dict>
      <key>comment</key>
      <string>.syntax .c, .syntax .c[ml]</string>
      <key>name</key>
      <string>Comment</string>
      <key>scope</key>
      <string>comment</string>
      <key>settings</key>
      <dict>
        <key>foreground</key>
        <string>{comment}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Comment.Preproc</string>
      <key>scope</key>
      <string>comment.block.preprocessor</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.syntax .cp</string>
        <key>fontStyle</key>
        <string>regular</string>
        <key>foreground</key>
        <string>{comment_special}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Comment.Special</string>
      <key>scope</key>
      <string>comment.documentation, comment.block.documentation</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.syntax .cs</string>
        <key>fontStyle</key>
        <string>regular</string>
        <key>foreground</key>
        <string>{comment_special}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Keyword.Constant</string>
      <key>scope</key>
      <string>constant.language, support.constant</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .kc</string>
        <key>foreground</key>
        <string>{keyword_constant}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Keyword.Type</string>
      <key>scope</key>
      <string>storage.type, support.type</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .kt</string>
        <key>fontStyle</key>
        <string>italic</string>
        <key>foreground</key>
        <string>{keyword_type}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Name.Builtin.Pseudo</string>
      <key>scope</key>
      <string>variable.language</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .bp</string>
        <key>foreground</key>
        <string>{builtin_pseudo}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Name.Class</string>
      <key>scope</key>
      <string>entity.name.type, entity.other.inherited-class, support.class</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .nc</string>
        <key>foreground</key>
        <string>{name_class}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Name.Constant</string>
      <key>scope</key>
      <string>variable.other.constant</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .no</string>
        <key>foreground</key>
        <string>{name_constant}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Name.Function</string>
      <key>scope</key>
      <string>entity.name.function, support.function, keyword.other.name-of-parameter</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .nf</string>
        <key>foreground</key>
        <string>{name_function}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Name.Tag</string>
      <key>scope</key>
      <string>entity.name.tag</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .nt</string>
        <key>foreground</key>
        <string>{tag_name}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Punctuation.Tag</string>
      <key>scope</key>
      <string>punctuation.definition.tag</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .nt</string>
        <key>foreground</key>
        <string>{tag_punctuation}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Name.Variable</string>
      <key>scope</key>
      <string>variable.parameter, support.variable</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .nv, .style .v[cgi]</string>
        <key>fontStyle</key>
        <string>italic</string>
        <key>foreground</key>
        <string>{name_variable}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Literal.Number</string>
      <key>scope</key>
      <string>constant.numeric, constant.other</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .m, .style .m[fhio], .style .il</string>
        <key>foreground</key>
        <string>{literal_number}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Literal.String</string>
      <key>scope</key>
      <string>string - string source, constant.character</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .s[bcd2ehixl]</string>
        <key>foreground</key>
        <string>{literal_string}</string>
      </dict>
    </dict>
    <dict>
      <key>name</key>
      <string>Literal.String.Regex</string>
      <key>scope</key>
      <string>string.regexp</string>
      <key>settings</key>
      <dict>
        <key>comment</key>
        <string>.style .sr</string>
        <key>foreground</key>
        <string>{literal_regex}</string>
      </dict>
    </dict>
  </array>
  <key>uuid</key>
  <string>7bceada4-bc39-429b-a6a9-9f7e95aaa436</string>
</dict>
</plist>
""".strip()
