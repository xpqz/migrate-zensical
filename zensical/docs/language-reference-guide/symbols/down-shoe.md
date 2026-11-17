---
search:
  exclude: true
---
# <span class="title-name">Down Shoe</span> `∪`

```apl
∪
```

Monadic Down Shoe means
[Unique](../primitive-functions/unique.md)
```apl
      ∪ 'ab' 'ba' 'ab' 1 1 2
┌──┬──┬─┬─┐
│ab│ba│1│2│
└──┴──┴─┴─┘
```
```apl
      mat
flywheel
shyster 
flywheel
     ∪mat
flywheel
shyster 
```

Dyadic Down Shoe means
[Union](../primitive-functions/union.md)
```apl
      'ab' 'cde' 'fg' ∪ 'a' 'ab'
┌──┬───┬──┬─┐
│ab│cde│fg│a│
└──┴───┴──┴─┘
```
[Language Elements](../glyphs.md)
