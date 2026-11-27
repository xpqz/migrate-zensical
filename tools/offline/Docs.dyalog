:Namespace Docs ⍝ v0.1
⍝ ]Docs installs, updates and reports the offline Dyalog documentation that F1 opens.
⍝ The bundle is dyalog-docs-<X.Y>.tar.gz with a sha256sum-format .sha256 beside it.
⍝ An update unpacks next to the installed copy and swaps it in with renames, so a
⍝ failed update leaves the installed copy as it was.

    :Section CONST ─────
    debug←0 ⋄ ⎕ML←1 ⋄ ⎕IO←1
    desc←'Install, update or report the offline documentation that F1 opens'
    files←'https://docs.dyalog.com/%V/files'

    :Namespace sys
        (os ver)←2↑# ⎕WG'APLVersion'
        os←3↑os
        ver←ver/⍨2>+\'.'=ver
        win←'Win'≡os
        mac←'Mac'≡os
        sep←'/\'⊃⍨1+win
    :EndNamespace
    :EndSection

    :Section IFACE ─────
    ∇ r←List
      r←⎕NS ⍬
      r.(Group Name Desc Parse)←'Experimental' 'Docs'desc'1S -version= -root= -system -from= -baseurl='
    ∇

      Help←{
          0=⍺:(⊂desc),Syntax ⍵
          r←0 ∇ ⍵
          b←3↑r
          a←2↓r
          a/⍨←~@(1+⍺)⊢1⍨¨a
          Fmt←{b,⍵,a}
          1=⍺:Fmt Details ⍵
          2=⍺:Fmt Examples ⍵
      }

      Run←{
          params←⊃⌽⍵
          verb←⎕C{0=≢⍵:'status' ⋄ ⊃⍵}params.Arguments
          o←⎕NS ⍬
          o.(version root system from baseurl)←params.(version root system from baseurl)
          debug∨←⎕SE.SALTUtils.DEBUG
          Show←{''⊣{⎕←⍵}¨⍵}
          'status'≡verb:Show Status o
          'update'≡verb:Show Install o
          11 Fail'Unknown action "',verb,'": use status or update'
      }

    ⍝ Install options → message lines. Fields (all optional): version (X.Y, default
    ⍝ the running interpreter's), root, system, from (a local archive), baseurl.
    ∇ r←Install o;dest;name;tmp;url;sum;want;archive;new;meta
      o←Defaults o
      dest←Dest o
      name←'dyalog-docs-',o.version,'.tar.gz'
      3 ⎕MKDIR Parent dest
      tmp←(Parent dest),'/.',(Leaf dest),'.update-',⎕A[?8⍴26]
      :Trap debug↓0
          3 ⎕MKDIR tmp
          :If ×≢o.from
              archive←o.from
              sum←archive,'.sha256'
              :If ~⎕NEXISTS sum
                  22 Fail'No ',(Leaf sum),' beside ',archive,' to verify it with'
              :EndIf
          :Else
              url←(BaseURL o),'/',name
              sum←(tmp,'/',name,'.sha256')Fetch url,'.sha256'
              archive←''
          :EndIf
          want←ReadSum sum
          :If want≡Installed dest
              r←(⊂'Documentation ',o.version,' is up to date'),Where dest
          :Else
              :If 0=≢archive
                  archive←(tmp,'/',name)Fetch url
              :EndIf
              :If want≢Sha256 archive
                  11 Fail'The checksum of ',archive,' does not match ',sum
              :EndIf
              Untar archive tmp
              new←tmp,'/dyalog-docs-',o.version
              :If ~⎕NEXISTS new,'/index.html'
                  11 Fail archive,' holds no dyalog-docs-',o.version,'/index.html'
              :EndIf
              meta←⎕JSON⊃⎕NGET new,'/bundle.json'
              meta.sha256←want
              (⊂(⎕UCS 13)(≠⊆⊢)⎕JSON⍠'Compact' 0⊢meta)⎕NPUT(new,'/bundle.json')1
              Swap new dest tmp
              r←(⊂'Installed documentation ',o.version,' built ',meta.build_date),Where dest
          :EndIf
      :Else
          Remove tmp
          ⎕SIGNAL⊂⎕DMX.(('EN'EN)('Message'Message))
      :EndTrap
      Remove tmp
    ∇

    ⍝ Status options → message lines describing each install location.
      Status←{
          o←Defaults ⍵
          dirs←{×≢o.root:,⊂Dest o ⋄ ∪(Dest o)(SystemDir o.version)}⍬
          ⊃,/Describe¨dirs
      }
    :EndSection

    :Section UHELP ─────
      Syntax←{
          r←,⊂'    ]',⍵,' [status|update] [-version=<X.Y>] [-root=<dir>] [-system] [-from=<archive>] [-baseurl=<url>]'
          r,←⊂''
          r,←⊂']',⍵,' -??   ⍝ for details'
          r,←⊂']',⍵,' -???  ⍝ for examples'
          r
      }
      Details←{
          r←,⊂'status    report the installed copies (default)'
          r,←⊂''
          r,←⊂'update    install the bundle, or replace an older copy; does nothing when the installed copy is current'
          r,←⊂''
          r,←⊂'-version= documentation version X.Y (default: this interpreter''s, ',sys.ver,')'
          r,←⊂''
          r,←⊂'-root=    install under <dir>/<X.Y> instead of the per-user location'
          r,←⊂''
          r,←⊂'-system   use the machine-wide location (Windows: <install dir>\help\docs); needs administrator rights'
          r,←⊂''
          r,←⊂'-from=    install from a local archive; its .sha256 must be beside it (no network needed)'
          r,←⊂''
          r,←⊂'-baseurl= download location (default ',files,', %V being the version)'
          r,←⊂''
          r,←⊂'Locations:'
          r,←⊂'  per-user      Windows %LOCALAPPDATA%\Dyalog\docs\<X.Y>; macOS ~/Library/Application Support/Dyalog/docs/<X.Y>; Linux ${XDG_DATA_HOME:-~/.local/share}/dyalog/docs/<X.Y>'
          r,←⊂'  machine-wide  Windows <install dir>\help\docs; macOS /Library/Application Support/Dyalog/docs/<X.Y>; Linux /usr/local/share/dyalog/docs/<X.Y>'
          r,←⊂''
          r,←⊂'F1 in RIDE opens the per-user copy, else the machine-wide one, unless DYALOGHELPDIR names a copy by its file:// URL (printed by update and status). The Windows IDE opens the bundle only once the interpreter supports file:// help.'
          r
      }
      Examples←{
          r←,⊂'Examples:'
          Eg←,/'    ]'⍵' ',⊂
          r,←Eg''
          r,←Eg'update'
          r,←Eg'update -version=20.0'
          r,←Eg'update -from=/media/usb/dyalog-docs-21.0.tar.gz'
          r,←Eg'update -root=/opt/dyalog-docs'
          r
      }
    :EndSection

    :Section UTILS ─────
    Env←{2 ⎕NQ'.' 'GetEnvironment'⍵}
    Leaf←{⊃⌽⍵⊆⍨~⍵∊'/\'}
    Parent←{⍵↑⍨¯1+⊃⌽⍸⍵∊'/\'}
    Remove←{0::0 ⋄ 3 ⎕NDELETE ⍵}
    Quote←{'"',⍵,'"'}
    ⍝ Signals in the caller, so a :Trap there catches it (⎕SIGNAL itself skips its own function's traps).
    Fail←{⎕SIGNAL⊂('EN'⍺)('Message'⍵)}

      Defaults←{
          o←⎕NS ⍬
          Get←⍵.{0=⎕NC ⍵:0 ⋄ ⍎⍵}
          Str←{0≡⍵:'' ⋄ ⍵}
          o.version←sys.ver{0=≢⍵:⍺ ⋄ ⍵}Str Get'version'
          o.(root from baseurl)←Str¨Get¨'root' 'from' 'baseurl'
          o.system←1≡Get'system'
          o
      }

      UserRoot←{
          sys.win:(Env'LOCALAPPDATA'),'\Dyalog\docs'
          sys.mac:(Env'HOME'),'/Library/Application Support/Dyalog/docs'
          xdg←Env'XDG_DATA_HOME'
          (xdg{×≢⍺:⍺ ⋄ ⍵}(Env'HOME'),'/.local/share'),'/dyalog/docs'
      }

      SystemDir←{
          sys.win:(Env'DYALOG'),'\help\docs'
          sys.mac:'/Library/Application Support/Dyalog/docs/',⍵
          '/usr/local/share/dyalog/docs/',⍵
      }

      Dest←{
          ×≢⍵.root:⍵.root,sys.sep,⍵.version
          ⍵.system:SystemDir ⍵.version
          (UserRoot ⍬),sys.sep,⍵.version
      }

      BaseURL←{
          base←⍵.baseurl{×≢⍺:⍺ ⋄ ⍵}files
          '%V'⎕R ⍵.version⊢base
      }

      FileURL←{
          p←'/'@('\'∘=)⍵
          sys.win:'file:///',p
          'file://',p
      }

      Where←{('  in ',⍵)('  for F1: DYALOGHELPDIR=',FileURL ⍵)}

      Installed←{
          f←⍵,'/bundle.json'
          ~⎕NEXISTS f:''
          m←⎕JSON⊃⎕NGET f
          2≠m.⎕NC'sha256':''
          m.sha256
      }

      Describe←{
          f←⍵,'/bundle.json'
          ~⎕NEXISTS f:,⊂'Not installed: ',⍵
          m←⎕JSON⊃⎕NGET f
          (⊂'Documentation ',m.version,' built ',m.build_date,' (',m.git,')'),Where ⍵
      }

      ReadSum←{⎕C 64↑⊃⊃⎕NGET ⍵ 1}

      Sha256←{
          sys.win:⎕C' '~⍨2⊃⎕SH'certutil -hashfile ',(Quote ⍵),' SHA256'
          sys.mac:64↑⊃⎕SH'shasum -a 256 ',Quote ⍵
          64↑⊃⎕SH'sha256sum ',Quote ⍵
      }

      Untar←{
          (archive dir)←⍵
          ⍬⊣⎕SH'tar -xzf ',(Quote archive),' -C ',Quote dir
      }

      Fetch←{
          url←⍵
          req←⎕SE.SALT.New'HttpCommand'('Get'url)
          req.OutFile←⍺ 1
          resp←req.Run
          0≠resp.rc:11 Fail resp.msg,' fetching ',url
          200=resp.HttpStatus:⍺
          11 Fail'HTTP ',(⍕resp.HttpStatus),' fetching ',url
      }

    ∇ Swap(new dest tmp);old
      old←tmp,'/previous'
      :If ⎕NEXISTS dest
          old ⎕NMOVE dest
      :EndIf
      :Trap 0
          dest ⎕NMOVE new
      :Else
          :If ⎕NEXISTS old
              dest ⎕NMOVE old
          :EndIf
          ⎕SIGNAL⊂⎕DMX.(('EN'EN)('Message'Message))
      :EndTrap
    ∇
    :EndSection

:EndNamespace
