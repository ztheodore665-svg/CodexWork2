using UnityEngine;

namespace PriestsAndDevils.Views
{
    public sealed class BoatView : MonoBehaviour
    {
        public void Initialize(Color color)
        {
            Renderer renderer = gameObject.GetComponent<Renderer>();
            if (renderer == null) renderer = gameObject.AddComponent<MeshRenderer>();
            Shader unlit = Shader.Find("Unlit/Color");
            if (unlit != null) renderer.material.shader = unlit;
            renderer.material.color = color;
        }
    }
}
