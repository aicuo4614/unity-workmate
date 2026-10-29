# Transparent Shader Family

Transparent Shader Family

Note. Unity 5 introduced the Standard Shader which replaces these shaders A program that runs on the GPU. Standard Shader which replaces these shaders A program that runs on the GPU. More info
See in Glossary .
The Transparent shaders are used for fully- or semi-transparent objects. Using the alpha channel of the Base texture, you can determine areas of the object which can be more or less transparent than others. This can create a great effect for glass, HUD interfaces, or sci-fi effects.
Transparent Vertex-Lit shader-TransVertexLit
Assets needed:
One Base texture with alpha channel for Transparency Map

» More details
Transparent Diffuse shader-TransDiffuse
Assets needed:
One Base texture with alpha channel for Transparency Map

» More details
Transparent Specular shader-TransSpecular
Assets needed:
One Base texture with alpha channel for combined Transparency Map/Specular Map

Note: One limitation of this shader is that the Base texture’s alpha channel doubles as a Specular Map for the Specular shaders in this family.
» More details
Transparent Normal mapped shader-TransBumpedDiffuse
Assets needed:
One Base texture with alpha channel for Transparency Map
One Normal map A type of Bump Map texture that allows you to add surface detail such as bumps, grooves, and scratches to a model which catch the light as if they are represented by real geometry.
See in Glossary normal map, no alpha channel required

» More details
Transparent Normal mapped Specular shader-TransBumpedSpecular
Assets needed:
One Base texture with alpha channel for combined Transparency Map/Specular Map
One Normal map normal map, no alpha channel required

Note: One limitation of this shader is that the Base texture’s alpha channel doubles as a Specular Map for the Specular shaders in this family.
» More details
Transparent Parallax shader-TransParallaxDiffuse
Assets needed:
One Base texture with alpha channel for Transparency Map
One Normal map normal map with alpha channel for Parallax Depth

» More details
Transparent Parallax Specular shader-TransParallaxSpecular
Assets needed:
One Base texture with alpha channel for combined Transparency Map/Specular Map
One Normal map normal map with alpha channel for Parallax Depth

Note: One limitation of this shader is that the Base texture’s alpha channel doubles as a Specular Map for the Specular shaders in this family.
» More details
TransparentFamily
