using UnityEngine;

namespace PriestsAndDevils.Views
{
    public sealed class Boat3DView : MonoBehaviour
    {
        public void Initialize(Color color)
        {
            Renderer renderer = GetComponent<Renderer>();
            if (renderer == null) renderer = gameObject.AddComponent<MeshRenderer>();
            Shader unlit = Shader.Find("Unlit/Color");
            if (unlit != null) renderer.material.shader = unlit;
            renderer.material.color = color;
        }
    }
}
