<img src="docs/icon.svg" alt="DeparturePixelZhBuilder icono de documentación" width="96" height="96">

# X-DeparturePixelZhBuilder

[English](README.md) · [简体中文](README.zh-Hans.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

Genera DeparturePixelZh y DeparturePixelZh Compact a partir de una receta versionada. Combina letras inglesas, caracteres chinos de estilo píxel e iconos para desarrolladores en una fuente monoespaciada.

Descarga los [paquetes de fuentes para escritorio o web](https://github.com/codingEzio/DeparturePixelZh-OSS/releases/tag/v0.1.1). Los registros originales sobre los derechos de algunos iconos difieren; consulta los [avisos de las fuentes](https://github.com/codingEzio/DeparturePixelZh-OSS/blob/main/NOTICE.md).

![DeparturePixelZh — letras inglesas y caracteres chinos de estilo píxel en una fuente monoespaciada](assets/departurepixelzh-social-card.png)

## Empezar

Instala [uv](https://docs.astral.sh/uv/) y coloca el repositorio de fuentes DeparturePixelZh en un directorio hermano. Para una compilación de publicación, usa la revisión del constructor fijada por la receta.

```sh
git clone https://github.com/codingEzio/DeparturePixelZh-OSS.git ../X-DeparturePixelZh
git checkout --detach "$(cat ../X-DeparturePixelZh/builder-version)"
uv run departurepixelzh-builder build --recipe ../X-DeparturePixelZh/recipe.json --output Build
uv run departurepixelzh-builder check --recipe ../X-DeparturePixelZh/recipe.json --output Build --release
uv run -m unittest discover -s tests
```

Para desarrollar el constructor localmente, añade `--development` a `build`. Esta opción omite la revisión fijada y no genera una versión candidata para publicación.

## Qué hace

La receta fija las URL de origen y los valores SHA-256. El constructor descarga los archivos desde esas URL o reutiliza copias en caché, y los valida con los valores fijados. Nunca lee fuentes instaladas en macOS como entrada de compilación. El constructor selecciona los glifos, los adapta a una cuadrícula común y genera TTF, WOFF2, mapas de cobertura, sumas de comprobación y registros de origen. Los caracteres latinos y los iconos ocupan una celda; los caracteres de ancho completo, dos. Compact usa celdas más estrechas. Los emoji quedan a cargo de la fuente alternativa del sistema.

[docs/how-it-works.md](docs/how-it-works.md) explica la selección de glifos y el espaciado.

## Otros comandos

- `install` instala fuentes verificadas y guarda un registro y copias de seguridad. Si una instalación se interrumpe, se revierte antes de continuar con la siguiente.
- `check-installed` compara los archivos instalados con el registro.
- `sync` copia fuentes verificadas, avisos y registros de origen según un manifiesto del proyecto consumidor. Se detiene si los archivos gestionados han cambiado fuera de la herramienta.
- `package --format desktop` crea un archivo TTF; `package --format web` crea uno WOFF2. El valor predeterminado `all` incluye ambos formatos.

Consulta los argumentos con `uv run departurepixelzh-builder --help` o con `--help` en cada comando. La instalación y la sincronización se detienen ante cambios inesperados para que la recuperación se gestione de forma explícita.

`package` requiere un sistema de archivos compatible con enlaces físicos. Si el archivo existente con el mismo nombre es idéntico byte por byte, puedes repetir el comando. Si su contenido es distinto, se conserva y el comando se detiene; usa otro destino.

Durante la sincronización, no modifiques los archivos del proyecto consumidor con otros programas. Tras una interrupción, un `sync` normal intenta la recuperación. Si informa de un conflicto o de un estado ambiguo, conserva los registros de recuperación y las copias de seguridad y revísalos manualmente. La recuperación tras la interrupción del proceso no garantiza la recuperación tras un corte de energía.

## Opcional: comparar las fuentes originales

En macOS con Homebrew, puedes instalar las fuentes originales para comparar su aspecto. No es un requisito de compilación ni hace falta para instalar o usar DeparturePixelZh.

```sh
brew install --cask font-departure-mono font-cubic-11
```

## Alcance y licencia

Este constructor sirve a la receta de DeparturePixelZh. No es un editor general de fuentes y no añade negrita, cursiva ni emoji en color.

El código original del constructor usa [MIT](LICENSE-MIT). Las fuentes generadas tienen requisitos independientes de OFL y de las licencias de sus componentes. Conserva los avisos del repositorio de fuentes al redistribuirlas.

[Comprobación y mantenimiento local del código](docs/maintenance.md).

Icono de documentación: codingEzio, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). [Alcance](docs/ARTWORK-LICENSE.md). Esta licencia cubre solo `docs/icon.svg`; las imágenes, fuentes, código y materiales de terceros existentes conservan sus propias condiciones.
