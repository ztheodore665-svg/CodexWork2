using UnityEngine;

namespace PriestsAndDevils.Views
{
    public sealed class Boat3DView : MonoBehaviour
    {
        public void Initialize(Color color)
        {
            Renderer renderer = GetComponent<Renderer>();
            if (renderer == null) renderer = gameObject.AddComponent<MeshRenderer>();
            Shader shader = Shader.Find("Unlit/Color");
            if (shader == null) shader = Shader.Find("Standard");
            if (shader != null) renderer.material = new Material(shader);
            renderer.material.color = color;
        }
    }
}
