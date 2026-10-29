# ShaderLab: assigning tags to a Pass

ShaderLab: assigning tags to a Pass

This page contains information on using a Tags block in your ShaderLab Unity’s language for defining the structure of Shader objects. More info
See in Glossary code to assign tags to a Pass. It also contains information on using the LightMode tag.
For information on how a Shader A program that runs on the GPU. More info
See in Glossary object works, and the relationship between Shader objects An instance of the Shader class, a Shader object is container for shader programs and GPU instructions, and information that tells Unity how to use them. Use them with materials to determine the appearance of your scene. Glossary object works, and the relationship between Shader objects An instance of the Shader class, a Shader object is container for shader programs and GPU instructions, and information that tells Unity how to use them. Use them with materials to determine the appearance of your scene. More info
See in Glossary , SubShaders and Passes, see Shader object fundamentals .
Overview
Tags are key-value pairs of data that you can assign to a Pass. Unity uses predefined tags and values to determine how and when to render a given Pass. You can also create your own custom Pass tags with custom values, and access them from C# code.
The most commonly used predefined Pass tag is the LightMode tag; this is used in all render pipelines. Other Pass tags vary by render pipeline A series of operations that take the contents of a Scene, and displays them on a screen. Unity lets you choose from pre-built render pipelines, or write your own. More info
See in Glossary . For more information, see the following pages:
For predefined Pass tags in the Built-in Render Pipeline, see Predefined Pass tags in the Built-in Render Pipeline .
For predefined Pass tags in the Universal Render Pipeline (URP), see URP Pass tags .

Render pipeline compatibility
Feature name Built-in Render Pipeline Universal Render Pipeline (URP) High Definition Render Pipeline (HDRP) Custom SRP
ShaderLab: Pass Tags block Yes Yes Yes Yes
ShaderLab: LightMode Pass tag Yes Yes Yes Yes

Using the Tags block
To assign Tags to a Pass in ShaderLab, you place a Tags block inside a Pass block.
Note that both SubShaders and Passes use the Tags block, but they work differently. Assigning SubShader tags to a Pass has no effect, and vice versa. The difference is where you put the Tags block:
To define Pass tags, place the Tags block inside a Pass block.
To define SubShader tags, place the Tags block inside a SubShader block but outside a Pass block.

For information on assigning tags to a SubShader, see Assigning tags to a SubShader .
Signature Function
Tags {"<name1>" = "<value1>" "<name2>" = "<value2>"} Applies the given tags to the Pass.

You can define as many tags as you like.

Using Pass tags with C# scripts
To access the value of a Pass tag from C# scripts A piece of code that allows you to create your own Components, trigger game events, modify Component properties over time and respond to user input in any way you like. More info
See in Glossary , you can use the Shader.FindPassTagValue API. This works for Unity’s predefined Pass tags, and for custom Pass tags that you have created.
Note: There are several APIs that work directly with the LightMode Pass tag. For more information, see Using the LightMode tag with C# scripts .

LightMode tag
The LightMode tag is a predefined Pass tag that Unity uses to determine whether to execute the Pass during a given frame, when during the frame Unity executes the Pass, and what Unity does with the output.
Note: The LightMode tag is not related to the LightMode enum, which relates to lighting.
Every render pipeline uses the LightMode tag, but the predefined values and their meanings vary. For more information, see Syntax and valid values .
In the Built-in Render Pipeline, if you do not set a LightMode tag, Unity renders the Pass without any lighting or shadows; this essentially the same as having a LightMode value of Always . In the Scriptable Render Pipeline, you can use the SRPDefaultUnlit value to reference Passes without a LightMode tag.

Syntax and valid values
Signature Function
“LightMode” = “[value]” Sets the LightMode value for this Pass.

Valid values for this tag depend on the render pipeline.
For LightMode Pass tag values in the Built-in Render Pipeline, see Predefined Pass tags in the Built-in Render Pipeline .
For LightMode Pass tag values in the Universal Render Pipeline (URP), see URP Pass tags: LightMode .

Using the LightMode tag with C# scripts
Material.SetShaderPassEnabled and ShaderTagId use the value of the LightMode tag to determine how Unity handles a given Pass.
In the Scriptable Render Pipeline, you can create custom values for the LightMode tag. You can then use these custom values to determine which Passes to draw during a given call to ScriptableRenderContext.DrawRenderers , by configuring a DrawingSettings struct. For more information and a code example, see Creating a simple render loop in a custom Scriptable Render Pipeline .
Examples
Shader "Examples/ExampleLightMode"
{
SubShader
{
Pass
{
Tags { "LightMode" = "Always" }

// The rest of the code that defines the Pass goes here.
}
}
}
