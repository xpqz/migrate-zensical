---
search:
  exclude: true
---
# <span class="title-name">Jot Underbar</span> `⍛`

```apl
⍛
```

Jot Underbar is a dyadic operator

Operator Jot Underbar means
[Behind](../primitive-operators/behind.md)
```apl
      ⍝ Is it a palindrome?
      ⌽⍛≡ 'Dyalog' 
0
      ⌽⍛≡ 'racecar'
1

      ⍝ Drop from the right
      4-⍛↓'Dyalog APL'
Dyalog
```
[Language Elements](../glyphs.md)
