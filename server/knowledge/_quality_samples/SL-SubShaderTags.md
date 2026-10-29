# ShaderLab: assigning tags to a SubShader

ShaderLab: assigning tags to a SubShader

This page contains information on using a Tags block in your ShaderLab Unity’s language for defining the structure of Shader objects. More info
See in Glossary code to assign tags to a SubShader.
For information on defining SubShader, see ShaderLab: defining a SubShader . For information on how a Shader A program that runs on the GPU. ShaderLab: defining a SubShader . For information on how a Shader A program that runs on the GPU. More info
See in Glossary object works, and the relationship between Shader objects, SubShaders and Passes, see Shader objects An instance of the Shader class, a Shader object is container for shader programs and GPU instructions, and information that tells Unity how to use them. Use them with materials to determine the appearance of your scene. Glossary object works, and the relationship between Shader objects, SubShaders and Passes, see Shader objects An instance of the Shader class, a Shader object is container for shader programs and GPU instructions, and information that tells Unity how to use them. Use them with materials to determine the appearance of your scene. More info
See in Glossary .
Overview
Tags are key-value pairs of data. Unity uses predefined keys and values to determine how and when to use a given SubShader, or you can also create your own custom SubShader tags with custom values. You can access SubShader tags from C# code.
Render pipeline compatibility
Feature name Built-in Render Pipeline A series of operations that take the contents of a Scene, and displays them on a screen. Unity lets you choose from pre-built render pipelines, or write your own. More info
See in Glossary Universal Render Pipeline (URP) High Definition Render Pipeline (HDRP) Custom SRP
ShaderLab: SubShader Tags block Yes Yes Yes Yes
ShaderLab: RenderPipeline SubShader tag No Yes Yes No
ShaderLab: Queue SubShader tag Yes Yes Yes

Yes

Note: in a custom SRP, you can define your own rendering order and choose whether or not to use render queues. For more information, see DrawingSettings and SortingCriteria.
ShaderLab: RenderType SubShader tag Yes Yes Yes Yes
ShaderLab: DisableBatching SubShader tag Yes Yes Yes Yes
ShaderLab: ForceNoShadowCasting SubShader tag Yes Yes Yes

This disables regular shadows, but has no effect on contact shadows. Yes
ShaderLab: CanUseSpriteAtlas SubShader tag Yes Yes Yes Yes
ShaderLab: PreviewType SubShader tag Yes Yes Yes Yes

Using the Tags block in a SubShader
In ShaderLab, you assign tags to a SubShader by placing a Tags block inside a SubShader block.
Note that both SubShaders and Passes use the Tags block, but they work differently. Assigning SubShader tags to a Pass has no effect, and vice versa. The difference is where you put the Tags block:
To define Pass tags, place the Tags block inside a Pass block.
To define SubShader tags, place the Tags block inside a SubShader block but outside a Pass block.

For information on assigning tags to a Pass, see Assigning tags to a Pass .
Signature Function
Tags { “[name1]” = “[value1]” “[name2]” = “[value2]”} Applies the given tags to the SubShader.

You can define as many tags as you like.

Using SubShader tags with C# code
You can read SubShader tags from a C# script using the Material.GetTag API, like this:
using UnityEngine;

public class Example : MonoBehaviour
{
// Attach this to a gameObject that has a Renderer component
string tagName = "ExampleTagName";

void Start()
{
Renderer myRenderer = GetComponent<Renderer>();
string tagValue = myRenderer.material.GetTag(ExampleTagName, true, "Tag not found");
Debug.Log(tagValue);
}
}
RenderPipeline tag
The RenderPipeline tag tells Unity whether a SubShader is compatible with the Universal Render Pipeline (URP) or the High Definition Render Pipeline (HDRP).
Syntax and valid values
Signature Function
“RenderPipeline” = “[name]” Tells Unity whether this SubShader is compatible with URP or HDRP.

Parameter Value Function
[name] UniversalPipeline This SubShader is compatible with URP only.
HDRenderPipeline This SubShader is compatible with HDRP only.
(any other value, or not declared) This SubShader is not compatible with URP or HDRP.

Examples
This example code declares that a SubShader is compatible with URP:
Shader "ExampleShader" {
SubShader {
Tags { "RenderPipeline" = "UniversalPipeline" }
Pass {
…
}
}
}
Queue tag
The Queue tag tells Unity which render queue to use for geometry that it renders. The render queue is one of the factors that determines the order that Unity renders geometry in.
Syntax and valid values
You can use the Queue tag in two ways: you can tell Unity to use a named render queue, or an unnamed render queue that it renders after a named render queue.
Signature Function
“Queue” = “[queue name]” Use the named render queue.
“Queue” = “[queue name] + [offset]” Use an unnamed queue, at a given offset from the named queue.

An example of when this is useful is in the case of transparent water, which you should draw after opaque objects but before transparent objects.

Signature Value Function
[queue name] Background Specifies the Background render queue.
Geometry Specifies the Geometry render queue.
AlphaTest Specifies the AlphaTest render queue.
Transparent Specifies the Transparent render queue.
Overlay Specifies the Overlay render queue.
[offset] integer Specifies the index at which Unity renders the unnamed queue, relative to the named queue.

Using this tag with C# code
You can use Shader.renderQueue to read the Queue tag value of a Shader object’s active SubShader.
By default, Unity renders geometry in the render queue specified in the [Queue] tag. You can override this value on a per-material basis. In the Unity Editor, you can do this in the material Inspector by setting the Render Queue property. In a C# script, you can do this by setting the value of Material.renderQueue using the Rendering.RenderQueue enum.
Examples
This example code creates a SubShader that renders geometry as part of the Transparent render queue:
Shader "ExampleShader" {
SubShader {
Tags { "Queue" = "Transparent" }
Pass {
…
}
}
}
This example code creates a SubShader that renders geometry in an unnamed queue, after the Geometry queue.
Shader "ExampleShader" {
SubShader {
Tags { "Queue" = "Geometry+1" }
Pass {
…
}
}
}
RenderType tag
Use the RenderType tag to override the behavior of a Shader object.
In the Built-in Render Pipeline, you can swap SubShaders at runtime using a technique called shader replacement . This technique works by identifying SubShaders that have matching RenderType tag values. This is used in some cases to produce camera’s depth texture .
In a render pipeline based on the Scriptable Render Pipeline, you can override the render state defined in a Shader object using a RenderStateBlock struct. You can use the value of the RenderType tag to identify SubShaders to override.
Syntax and valid values
Signature Function
“RenderType” = “[renderType]” Set the RenderType value for this SubShader.

Signature Value Function
[renderType] String There are no set values for this parameter. To identify the RenderType value for any SubShader that you want to replace, open its shader source file.

The RenderType SubShader tags for Unity’s legacy built-in shaders are listed on the shader replacement page .

You can also create your own values for your custom SubShaders.

Using this tag with C# code
For information on shader replacement in the Built-in Render Pipeline, see Shader replacement . For information on using a RenderStateBlock in the Scriptable Render Pipeline, see the API documentation for ScriptableRenderContext.DrawRenderers .
Examples
This example code creates a SubShader with a RenderType value of TransparentCutout :
Shader "ExampleShader" {
SubShader {
Tags { "RenderType" = "TransparentCutout" }
Pass {
…
}
}
}
ForceNoShadowCasting tag
The ForceNoShadowCasting tag prevents geometry in a SubShader from casting (and sometimes receiving) shadows. The exact behavior depends on the render pipeline and rendering path The technique that a render pipeline uses to render graphics. Choosing a different rendering path affects how lighting and shading are calculated. Some rendering paths are more suited to different platforms and hardware than others. More info
See in Glossary .
This can be useful if you are using shader replacement , but you do not want to inherit a shadow pass from another SubShader.
Syntax and valid values
Signature Function
“ForceNoShadowCasting” = “[state]” Whether to prevent shadow casting (and sometimes receiving) for all geometry that uses this SubShader.

Signature Value Function
[state] True Unity prevents the geometry in this SubShader from casting shadows.

In the Built in Render Pipeline, with the Forward or Legacy Vertex Lit rendering paths, Unity also prevents the geometry in this SubShader from receiving shadows.

In HDRP, this does not prevent the geometry from casting contact shadows.
False Unity does not prevent the geometry in this SubShader from casting or receiving shadows. This is the default value.

Examples
This example code creates a SubShader with a ForceNoShadowCasting value of True :
Shader "ExampleShader" {
SubShader {
Tags { "ForceNoShadowCasting" = "True" }
Pass {
…
}
}
}
DisableBatching tag
The DisableBatching SubShader Tag prevents Unity from applying Dynamic Batching An automatic Unity process which attempts to render multiple meshes as if they were a single mesh for optimized graphics performance. The technique transforms all of the GameObject vertices on the CPU and groups many similar vertices together. Dynamic Batching An automatic Unity process which attempts to render multiple meshes as if they were a single mesh for optimized graphics performance. The technique transforms all of the GameObject vertices on the CPU and groups many similar vertices together. More info
See in Glossary to geometry that uses this SubShader.
This is useful for shader programs that perform object space operations. Dynamic Batching transforms all geometry into world space, meaning that shader programs can no longer access object space. Shader programs that rely on object space therefore do not render correctly. To avoid this problem, use this SubShader Tag to prevent Unity from applying Dynamic Batching.
Syntax and valid values
Signature Function
“DisableBatching” = “[state]” Whether Unity prevents Dynamic Batching for all geometry that uses this SubShader.

Signature Value Function
[state] True Unity prevents Dynamic Batching for geometry that uses this SubShader.
False Unity does not prevent Dynamic Batching for geometry that uses this SubShader. This is the default value.
LODFading Unity prevents Dynamic Batching for all geometry that is part of a LODGroup with a Fade Mode value that is not None. Otherwise, Unity does not prevent Dynamic Batching.

Examples
This example code creates a SubShader with a DisableBatching value of True :
Shader "ExampleShader" {
SubShader {
Tags { "DisableBatching" = "True" }
Pass {
…
}
}
}
IgnoreProjector tag
In the Built-in Render Pipeline, the IgnoreProjector SubShader tag tells Unity whether geometry is affected by Projectors . This is mostly useful for excluding semi-transparent geometry, which Projectors are not compatible with.
This tag has no effect in other render pipelines.
Syntax and valid values
Signature Function
“IgnoreProjector” = “[state]” Whether Unity ignores Projectors when rendering this geometry.

Signature Value Function
[state] True Unity ignores Projectors when rendering this geometry.
False Unity does not ignore Projectors when rendering this geometry. This is the default value.

Examples
This example code creates a SubShader with an IgnoreProjectors value of True :
Shader "ExampleShader" {
SubShader {
Tags { "IgnoreProjector" = "True" }
Pass {
…
}
}
}
PreviewType tag
The PreviewType SubShader Tag tells the Unity Editor how to display a material that uses this SubShader in the Material Inspector A Unity window that displays information about the currently selected GameObject, asset or project settings, allowing you to inspect and edit the values. More info
See in Glossary .
Syntax and valid values
Signature Function
“PreviewType” = “[shape]” Which shape the Unity Editor uses to display a preview of a material that uses this SubShader.

Signature Value Function
[shape] Sphere Display the material on a sphere. This is the default value.
Plane Display the material on a plane.
Skybox A special type of Material used to represent skies. Usually six-sided. More info
See in Glossary Display the material on a skybox.

Examples
This example code creates a SubShader with an PreviewType value of Plane :
Shader "ExampleShader" {
SubShader {
Tags { "PreviewType" = "Plane" }
Pass {
…
}
}
}
CanUseSpriteAtlas tag
Use this SubShader tag in projects that use the Legacy Sprite Packer to warn users that your shader relies on original texture coordinates, and that they should therefore not pack its textures into atlases.
Syntax and valid values
Signature Function
“CanUseSpriteAtlas” = “[state]” Whether the Sprites that use this SubShader are compatible with the Legacy Sprite A 2D graphic objects. If you are used to working in 3D, Sprites are essentially just standard textures but there are special techniques for combining and managing sprite textures for efficiency and convenience during development. More info
See in Glossary Packer.

Signature Value Function
[state] True Sprites that use this SubShader are compatible with the Legacy Sprite Packer A facility that packs graphics from several sprite textures tightly together within a single texture known as an atlas. Unity provides a Sprite Packer utility to automate the process of generating atlases from the individual sprite textures. More info
See in Glossary . This is the default value.
False Sprites that use this SubShader are not compatible with the Legacy Sprite Packer.

When a SubShader with a CanUseSpriteAtlas value of False is used with a Sprite that has a Legacy Sprite Packer packing tag, Unity shows an error message in the Inspector.

CanUseSpriteAtlas tag code examples
This example code creates a SubShader with an CanUseSpriteAtlas value of False :
Shader "ExampleShader" {
SubShader {
Tags { "CanUseSpriteAtlas" = "False" }
Pass {
…
}
}
}
